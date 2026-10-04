"""
Renders ONE scene into a finished clip (picture + voices).

Deliberately free of any database / Celery code: it takes plain objects and a
`ctx` that provides stop-checking, so the whole thing can be tested with fake
AI servers (see scripts/selftest_renderer.py).

Pipeline for a scene
--------------------
1. Voices   - each narration / dialogue line is spoken by the right voice
              (male/female, per character) -> real durations.
2. Timeline - lines are laid out with natural pauses; the scene's length is
              whatever the speech needs.
3. Shots    - the timeline is cut into short shots (a few seconds each).
4. Pictures - a wide keyframe for the scene + a close-up keyframe for every
              speaking character (big sharp faces -> far less 'melting').
5. Motion   - each shot is animated from its keyframe by the video model,
              then fitted to its exact length, cropped (never stretched) to
              16:9 and sharpened.
6. Mix      - shots are joined and the voice track is laid under them.

Everything produced is cached inside the scene folder together with a
fingerprint of its inputs, so Resume only redoes what is missing or changed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.core.config import settings
from app.services import prompt_builder as pb
from app.services.shot_planner import Segment, Shot, layout_timeline, plan_shots, LEAD_IN
from app.services.voice_service import (
    NARRATOR_NAME,
    VoiceCast,
    clean_for_tts,
    is_narrator,
    limit_words,
)

logger = logging.getLogger(__name__)

STAGE_IMAGES = "GENERATING_IMAGES"
STAGE_AUDIO = "GENERATING_AUDIO"
STAGE_VIDEOS = "GENERATING_VIDEOS"
STAGE_ASSEMBLE = "GENERATING_VIDEOS"


def _seed(*parts: Any) -> int:
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:24] or "x"


def _nonempty(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


@dataclass
class SceneResult:
    final_path: str
    wide_keyframe: str
    duration: float
    shots: int
    reused: int


class SceneRenderer:
    def __init__(self, image_service, video_service, tts_service, ffmpeg_service):
        self.image = image_service
        self.video = video_service
        self.tts = tts_service
        self.ff = ffmpeg_service

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _characters_on_screen(scene, characters: Sequence) -> List:
        present = [str(n).strip().lower() for n in (getattr(scene, "characters_present", None) or [])]
        chosen = [c for c in characters if c.name.strip().lower() in present]
        return chosen or list(characters)

    def _fingerprint(self, scene, characters: Sequence, style: str, cast: VoiceCast) -> str:
        blob = json.dumps(
            {
                "v": 3,
                "img": scene.image_prompt,
                "motion": scene.motion_prompt,
                "vis": scene.visual_description,
                "loc": scene.location,
                "narr": scene.narration_text,
                "dur": scene.duration_seconds,
                "turns": scene.dialogue_turns,
                "chars": [(c.name, c.appearance_prompt, c.description) for c in characters],
                "style": style,
                "voices": cast.signature(),
                "size": [settings.IMAGE_WIDTH, settings.IMAGE_HEIGHT, settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT],
                "shots": [settings.VIDEO_SHOT_SECONDS, settings.VIDEO_MAX_SHOTS_PER_SCENE, settings.SHOT_COVERAGE],
                "sharp": settings.OUTPUT_SHARPEN,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha1(blob.encode()).hexdigest()

    def _prepare_dir(self, scene_dir: str, fingerprint: str) -> bool:
        """Returns True if previously rendered files in the folder may be reused."""
        marker = os.path.join(scene_dir, "fingerprint.txt")
        reusable = False
        if os.path.isdir(scene_dir) and os.path.exists(marker):
            try:
                reusable = open(marker).read().strip() == fingerprint
            except OSError:
                reusable = False
        if not reusable and os.path.isdir(scene_dir):
            shutil.rmtree(scene_dir, ignore_errors=True)
        os.makedirs(scene_dir, exist_ok=True)
        with open(marker, "w") as f:
            f.write(fingerprint)
        return reusable

    def _segments(self, scene, cast: VoiceCast) -> List[Segment]:
        turns = [t for t in (scene.dialogue_turns or []) if clean_for_tts(t.get("text", ""), t.get("speaker", ""))]
        narration = clean_for_tts(scene.narration_text or "")
        segments: List[Segment] = []

        if narration:
            segments.append(
                Segment(
                    index=0, speaker=NARRATOR_NAME, kind="narration",
                    text=limit_words(narration, 22 if turns else 45),
                )
            )
        for t in turns:
            speaker = (t.get("speaker") or "").strip() or "Character"
            if is_narrator(speaker):
                kind, speaker = "narration", NARRATOR_NAME
            else:
                kind = "dialogue"
            segments.append(
                Segment(
                    index=len(segments), speaker=speaker, kind=kind,
                    text=limit_words(clean_for_tts(t.get("text", ""), speaker), 40),
                    expression=t.get("expression") or "", action=t.get("action") or "",
                )
            )
        if not segments:
            segments.append(Segment(index=0, speaker=NARRATOR_NAME, kind="ambient"))
        return segments

    # ------------------------------------------------------------------ main
    def render(
        self,
        scene,
        characters: Sequence,
        style: str,
        cast: VoiceCast,
        scene_dir: str,
        project_id: str,
        ctx,
    ) -> SceneResult:
        W, H, FPS = settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT, settings.VIDEO_FPS
        reused = 0

        fp = self._fingerprint(scene, characters, style, cast)
        may_reuse = self._prepare_dir(scene_dir, fp)
        final_path = os.path.join(scene_dir, "scene_final.mp4")
        if may_reuse and _nonempty(final_path):
            wide = os.path.join(scene_dir, "keyframe.png")
            total = self.ff.get_duration(final_path)
            return SceneResult(final_path, wide, total, 0, 0)

        on_screen = self._characters_on_screen(scene, characters)
        segments = self._segments(scene, cast)

        # ---------------------------------------------------------- 1. voices
        ctx.stage(STAGE_AUDIO, 0.02, "Recording voices")
        for i, seg in enumerate(segments):
            ctx.checkpoint()
            seg.audio_path = os.path.join(scene_dir, f"seg_{i}_{_slug(seg.speaker)}.mp3")
            if seg.kind == "ambient":
                seconds = float(max(3, min(int(scene.duration_seconds or 5), 8)))
                if not _nonempty(seg.audio_path):
                    self._silent_mp3(seg.audio_path, seconds)
                seg.duration = seconds
            else:
                if may_reuse and _nonempty(seg.audio_path):
                    reused += 1
                else:
                    profile = cast.profile(seg.speaker, seg.expression, seg.action)
                    ctx.call(self.tts.synthesize, seg.text, profile, seg.audio_path, seg.speaker)
                seg.duration = self.ff.get_duration(seg.audio_path)
            ctx.stage(STAGE_AUDIO, 0.02 + 0.08 * (i + 1) / len(segments), f"Recording voices ({i + 1}/{len(segments)})")

        # --------------------------------------------------------- 2/3. timeline + shots
        total = layout_timeline(segments)
        shots = plan_shots(
            segments, total,
            max_len=settings.VIDEO_SHOT_SECONDS,
            max_shots=settings.VIDEO_MAX_SHOTS_PER_SCENE,
            hard_max_len=settings.VIDEO_MAX_CLIP_SECONDS + 1.2,
        )

        # ---------------------------------------------------------- 4. keyframes
        ctx.stage(STAGE_IMAGES, 0.10, "Drawing the scene")
        wide_path = os.path.join(scene_dir, "keyframe.png")
        if may_reuse and _nonempty(wide_path):
            reused += 1
        else:
            ctx.checkpoint()
            ctx.call(
                self.image.generate_image,
                pb.keyframe_prompt(scene.image_prompt or scene.visual_description, on_screen, style),
                wide_path, pb.image_negative(style), None, None, _seed(project_id, scene.scene_number, "wide"),
            )
        ctx.set_scene_image(wide_path)

        keyframe_for_speaker: Dict[str, str] = {}
        if settings.SHOT_COVERAGE:
            speakers = []
            for sh in shots:
                if sh.kind == "dialogue" and sh.speaker not in speakers:
                    speakers.append(sh.speaker)
            for n, name in enumerate(speakers):
                ctx.stage(STAGE_IMAGES, 0.20 + 0.15 * n / max(1, len(speakers)), f"Drawing close-up of {name}")
                member = next((c for c in characters if c.name.strip().lower() == name.strip().lower()), None)
                if member is None:  # fuzzy: "Obinna (V.O.)" -> Obinna
                    m = cast.member_for(name)
                    member = next((c for c in characters if c.name.strip().lower() == m.name.strip().lower()), None)
                if member is None:
                    continue  # an extra with no appearance description: use the wide shot
                path = os.path.join(scene_dir, f"closeup_{_slug(member.name)}.png")
                if may_reuse and _nonempty(path):
                    reused += 1
                else:
                    expression = next((s.expression for s in shots if s.speaker == name and s.expression), "")
                    ctx.checkpoint()
                    ctx.call(
                        self.image.generate_image,
                        pb.closeup_prompt(member, expression, scene.location, style),
                        path, pb.image_negative(style), None, None, _seed(project_id, member.name, "face"),
                    )
                keyframe_for_speaker[name] = path
        ctx.stage(STAGE_IMAGES, 0.35, "Keyframes ready")

        # ---------------------------------------------------------- 5. motion
        fitted: List[str] = []
        for sh in shots:
            ctx.checkpoint()
            ctx.stage(
                STAGE_VIDEOS,
                0.35 + 0.55 * sh.index / len(shots),
                f"Animating shot {sh.index + 1}/{len(shots)}",
            )
            raw = os.path.join(scene_dir, f"shot_{sh.index}_raw.mp4")
            fit = os.path.join(scene_dir, f"shot_{sh.index}.mp4")
            if may_reuse and _nonempty(raw):
                reused += 1
            else:
                source = keyframe_for_speaker.get(sh.speaker) or wide_path
                prompt = pb.video_prompt(
                    sh.kind, sh.speaker, sh.expression, sh.action,
                    scene.motion_prompt, scene.visual_description, sh.index,
                )
                request_len = min(sh.duration + 0.4, settings.VIDEO_MAX_CLIP_SECONDS)
                ctx.call(
                    self.video.generate_clip, source, raw, prompt, request_len,
                    _seed(project_id, scene.scene_number, sh.index, "clip"),
                )
            ctx.call(self.ff.fit_clip, raw, fit, sh.duration, W, H, FPS, settings.OUTPUT_SHARPEN)
            fitted.append(fit)

        # ---------------------------------------------------------- 6. mix
        ctx.checkpoint()
        ctx.stage(STAGE_ASSEMBLE, 0.93, "Mixing audio and picture")
        wav = os.path.join(scene_dir, "voices.wav")
        self.ff.build_audio_track(
            [s.audio_path for s in segments], [s.gap_after for s in segments], wav, lead_in=LEAD_IN,
        )
        self.ff.assemble_scene(fitted, wav, final_path)
        ctx.stage(STAGE_ASSEMBLE, 1.0, "Scene done")

        return SceneResult(final_path, wide_path, total, len(shots), reused)

    # ------------------------------------------------------------------ misc
    def _silent_mp3(self, path: str, seconds: float) -> None:
        import subprocess

        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.2f}",
             "-codec:a", "libmp3lame", "-q:a", "9", path],
            check=True, capture_output=True,
        )
