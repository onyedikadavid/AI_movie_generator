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
    chunk_speech,
    clean_for_tts,
    estimate_speech_seconds,
    is_narrator,
    limit_words,
    merge_actions,
    normalize_turn,
)

logger = logging.getLogger(__name__)

STAGE_IMAGES = "GENERATING_IMAGES"
STAGE_AUDIO = "GENERATING_AUDIO"
STAGE_VIDEOS = "GENERATING_VIDEOS"
STAGE_ASSEMBLE = "GENERATING_VIDEOS"
AMBIENCE_CROSSFADE = 0.12   # seconds of overlap when joining the background sound of consecutive shots


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

    def _fingerprint(self, scene, characters: Sequence, style: str, cast: VoiceCast, video_mode: str) -> str:
        blob = json.dumps(
            {
                "v": 5,
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
                "camera": settings.VIDEO_CAMERA_MOTION,
                "interp": settings.VIDEO_INTERPOLATE,
                "mode": video_mode,
                "narrator": bool(settings.NARRATOR_ENABLED),
                "scenemode": [settings.SCENE_MODE, settings.WHOLE_SCENE_MAX_SECONDS],
                "audio": [settings.AUDIO_MODE, settings.AMBIENCE_VOLUME, getattr(scene, "sound_design", None), settings.MODEL_VOICE_MAX_WORDS],
                "t2vres": settings.VIDEO_T2V_RESOLUTION,
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

    def _segments(self, scene, cast: VoiceCast, model_audio: bool = False) -> List[Segment]:
        # Each line is first split into ACTION (performed on screen) and WORDS (the only thing ever spoken).
        turns = []
        for raw in (scene.dialogue_turns or []):
            t = normalize_turn(raw)
            if clean_for_tts(t.get("text", ""), t.get("speaker", "")):
                turns.append(t)
        narrator_on = bool(settings.NARRATOR_ENABLED)
        if not narrator_on:
            # Characters only: nothing is spoken by a narrator, not even lines the script labelled "Narrator".
            turns = [t for t in turns if not is_narrator((t.get("speaker") or "").strip())]
        narration = clean_for_tts(scene.narration_text or "") if narrator_on else ""
        whole = (settings.SCENE_MODE or "shots").strip().lower() == "whole"
        chunk_words = 60 if whole else settings.MODEL_VOICE_MAX_WORDS   # whole scene: lines stay whole
        segments: List[Segment] = []

        def add(speaker: str, kind: str, text: str, expression: str = "", action: str = "") -> None:
            if model_audio:
                # The video model speaks the line inside one clip: split long lines at sentence ends.
                for n, piece in enumerate(chunk_speech(text, chunk_words) or [text]):
                    segments.append(Segment(index=len(segments), speaker=speaker, kind=kind, text=piece,
                                            expression=expression, action=action if n == 0 else ""))
            else:
                segments.append(Segment(index=len(segments), speaker=speaker, kind=kind, text=text,
                                        expression=expression, action=action))

        if narration:
            if model_audio:
                n_words = settings.MODEL_NARRATION_WORDS * (3 if whole else 1) * (1 if turns else 2)
            else:
                n_words = 22 if turns else 45
            add(NARRATOR_NAME, "narration", limit_words(narration, n_words))
        for t in turns:
            speaker = (t.get("speaker") or "").strip() or "Character"
            if is_narrator(speaker):
                kind, speaker = "narration", NARRATOR_NAME
            else:
                kind = "dialogue"
            add(speaker, kind, limit_words(clean_for_tts(t.get("text", ""), speaker), 40),
                t.get("expression") or "", t.get("action") or "")
        if not segments:
            segments.append(Segment(index=0, speaker=NARRATOR_NAME, kind="ambient"))
        return segments

    @staticmethod
    def _character_for(name: str, cast: VoiceCast, characters: Sequence):
        n = (name or "").strip().lower()
        member = next((c for c in characters if c.name.strip().lower() == n), None)
        if member is None and n and not is_narrator(name):
            m = cast.member_for(name)
            member = next((c for c in characters if c.name.strip().lower() == m.name.strip().lower()), None)
        return member

    # ------------------------------------------------------- whole-scene mode
    def _whole_parts(self, segments: List[Segment], cap: float) -> List[List[Segment]]:
        """Groups consecutive lines into the fewest pieces that each fit in `cap` seconds (a line is never cut)."""
        parts: List[List[Segment]] = []
        cur: List[Segment] = []
        used = 0.0
        for seg in segments:
            if seg.kind == "ambient":
                continue
            need = seg.duration + 0.5
            if cur and used + need > cap - 0.8:
                parts.append(cur)
                cur, used = [], 0.0
            cur.append(seg)
            used += need
        if cur:
            parts.append(cur)
        return parts

    def _render_whole(self, scene, characters, style, cast, scene_dir, project_id, ctx, segments, mode,
                      on_screen, may_reuse, final_path):
        """One clip per scene (or per `WHOLE_SCENE_MAX_SECONDS` piece of a long one). Returns None when the server
        could only render a fraction of the requested length, so the caller can use the shot-by-shot path."""
        W, H, FPS = settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT, settings.VIDEO_FPS
        cap = float(settings.WHOLE_SCENE_MAX_SECONDS)
        parts = self._whole_parts(segments, cap) or [[]]
        t2v = mode == "t2v"
        wide_path = os.path.join(scene_dir, "keyframe.png")
        ctx.stage(STAGE_AUDIO, 0.05, "Preparing the scene")
        source = None
        if not t2v:
            ctx.stage(STAGE_IMAGES, 0.10, "Drawing the scene")
            if not (may_reuse and _nonempty(wide_path)):
                ctx.checkpoint()
                ctx.call(
                    self.image.generate_image,
                    pb.keyframe_prompt(scene.image_prompt or scene.visual_description, on_screen, style),
                    wide_path, pb.image_negative(style), None, None, _seed(project_id, scene.scene_number, "wide"),
                )
            ctx.set_scene_image(wide_path)
            source = wide_path
        fitted: List[str] = []
        reused = 0
        for n, part in enumerate(parts):
            ctx.checkpoint()
            ctx.stage(STAGE_VIDEOS, 0.35 + 0.55 * n / len(parts), f"Generating the scene ({n + 1}/{len(parts)})")
            beats = []
            for seg in part:
                member = self._character_for(seg.speaker, cast, characters) if seg.kind == "dialogue" else None
                beats.append({
                    "kind": seg.kind, "speaker": seg.speaker, "expression": seg.expression, "action": seg.action,
                    "speech": seg.text, "voice_desc": cast.voice_description(seg.speaker),
                })
            talk = sum(sg.duration + 0.5 for sg in part)
            request_len = min(max(talk + 0.8, 4.0), cap)
            prompt = pb.whole_scene_prompt(
                beats, on_screen, scene.location, scene.visual_description, style, scene.motion_prompt,
                getattr(scene, "sound_design", None), start_image=bool(source and n > 0),
            )
            seed = _seed(project_id, scene.scene_number, "whole", n)
            raw = os.path.join(scene_dir, f"whole_{n}_raw.mp4")
            fit = os.path.join(scene_dir, f"whole_{n}.mp4")
            key = hashlib.sha1(
                f"whole|{mode}|{prompt}|{request_len:.2f}|{seed}|{os.path.basename(source or '')}".encode()
            ).hexdigest()
            key_file = raw + ".key"
            stored = open(key_file).read().strip() if os.path.exists(key_file) else None
            if may_reuse and _nonempty(raw) and stored == key:
                reused += 1
            else:
                if t2v:
                    ctx.call(self.video.generate_text_clip, prompt, raw, request_len, seed, True)
                else:
                    ctx.call(self.video.generate_clip, source, raw, prompt, request_len, seed, True)
                got = self.ff.get_duration(raw)
                if got < 0.8 * request_len:
                    for f in (raw, key_file):
                        if os.path.exists(f):
                            os.remove(f)
                    return None
                with open(key_file, "w") as fh:
                    fh.write(key)
            if n == 0 and t2v and not _nonempty(wide_path):
                ctx.call(self.ff.extract_frame, raw, wide_path)
                ctx.set_scene_image(wide_path)
            ctx.call(self.ff.normalize_clip, raw, fit, W, H, FPS, settings.OUTPUT_SHARPEN, settings.VIDEO_INTERPOLATE)
            fitted.append(fit)
        ctx.checkpoint()
        ctx.stage(STAGE_ASSEMBLE, 0.93, "Finishing the scene")
        if len(fitted) == 1:
            shutil.copyfile(fitted[0], final_path)
        else:
            self.ff.concatenate_videos(fitted, final_path, "aac")
        total = self.ff.get_duration(final_path)
        ctx.stage(STAGE_ASSEMBLE, 1.0, "Scene done")
        return SceneResult(final_path, wide_path, total, len(fitted), reused)

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
        video_mode: Optional[str] = None,
    ) -> SceneResult:
        """video_mode: "i2v" (draw a keyframe with the image model, then animate it) or "t2v" (text-to-video, no
        image model). None = the server-wide default (settings.VIDEO_MODE)."""
        W, H, FPS = settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT, settings.VIDEO_FPS
        reused = 0
        mode = (video_mode or settings.VIDEO_MODE or "i2v").strip().lower()
        mode = "t2v" if mode == "t2v" else "i2v"
        model_audio = (settings.AUDIO_MODE or "voices").strip().lower() == "model"
        free_cam = pb.is_free_camera(settings.VIDEO_CAMERA_MOTION)

        fp = self._fingerprint(scene, characters, style, cast, mode)
        may_reuse = self._prepare_dir(scene_dir, fp)
        final_path = os.path.join(scene_dir, "scene_final.mp4")
        if may_reuse and _nonempty(final_path):
            wide = os.path.join(scene_dir, "keyframe.png")
            total = self.ff.get_duration(final_path)
            return SceneResult(final_path, wide, total, 0, 0)

        on_screen = self._characters_on_screen(scene, characters)
        segments = self._segments(scene, cast, model_audio)

        # ---------------------------------------------------------- 1. voices
        ctx.stage(STAGE_AUDIO, 0.02, "Preparing the lines" if model_audio else "Recording voices")
        for i, seg in enumerate(segments):
            ctx.checkpoint()
            seg.audio_path = os.path.join(scene_dir, f"seg_{i}_{_slug(seg.speaker)}.mp3")
            if model_audio:
                # No text-to-speech: the video model speaks the line itself. Only its length is estimated here,
                # to size the clip; the real length is whatever the model produces.
                if seg.kind == "ambient":
                    seg.duration = float(max(3, min(int(scene.duration_seconds or 5), 10)))
                else:
                    seg.duration = estimate_speech_seconds(seg.text)
            elif seg.kind == "ambient":
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
            if not model_audio:
                ctx.stage(STAGE_AUDIO, 0.02 + 0.08 * (i + 1) / len(segments), f"Recording voices ({i + 1}/{len(segments)})")

        # --------------------------------------------------------- 2/3. timeline + shots
        total = layout_timeline(segments)
        shots = plan_shots(
            segments, total,
            max_len=settings.VIDEO_SHOT_SECONDS,
            max_shots=settings.VIDEO_MAX_SHOTS_PER_SCENE,
            hard_max_len=settings.VIDEO_MAX_CLIP_SECONDS + 1.2,
            one_per_segment=model_audio,
        )

        if model_audio and (settings.SCENE_MODE or "shots").strip().lower() == "whole":
            whole = self._render_whole(scene, characters, style, cast, scene_dir, project_id, ctx,
                                       segments, mode, on_screen, may_reuse, final_path)
            if whole is not None:
                return whole
            logger.warning("Scene %s: whole-scene clip came back too short; falling back to shots.", scene.scene_number)

        t2v = mode == "t2v"
        # Hybrid sound = the video model's own background sound mixed under the pipeline's voices.
        hybrid = t2v and (settings.AUDIO_MODE or "voices").lower() == "hybrid" and not model_audio
        ambience_parts: List[str] = []
        wide_path = os.path.join(scene_dir, "keyframe.png")
        keyframe_for_speaker: Dict[str, str] = {}

        if t2v:
            # Text-to-video: no keyframes at all. (A thumbnail is cut from the first clip below.)
            ctx.stage(STAGE_IMAGES, 0.35, "Text-to-video: no keyframes needed")
        else:
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
                            pb.closeup_prompt(member, expression, scene.location, style, free_cam),
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
            speech = sh.text if (model_audio and sh.kind in ("dialogue", "narration")) else ""
            voice_desc = cast.voice_description(sh.speaker) if speech else ""
            # Model voices need real room for the line plus a beat of breathing space; clips are capped by the server.
            request_len = min(sh.duration + (0.8 if model_audio else 0.4), settings.VIDEO_MAX_CLIP_SECONDS)
            if t2v:
                member = self._character_for(sh.speaker, cast, characters) if sh.kind == "dialogue" else None
                prompt = pb.text_video_prompt(
                    sh.kind, sh.speaker, sh.expression, sh.action,
                    getattr(member, "appearance_prompt", "") or getattr(member, "description", "") if member else "",
                    on_screen, scene.location, scene.visual_description, style, sh.index,
                    settings.VIDEO_CAMERA_MOTION, getattr(scene, "sound_design", None), hybrid,
                    scene_motion=scene.motion_prompt, speech=speech, voice_desc=voice_desc, model_voice=model_audio,
                )
                # Same seed for the same character = the best-effort way to keep a similar face between shots.
                seed = _seed(project_id, sh.speaker, "face") if sh.kind == "dialogue" else _seed(project_id, scene.scene_number, "wide")
                source = None
            else:
                source = keyframe_for_speaker.get(sh.speaker) or wide_path
                prompt = pb.video_prompt(
                    sh.kind, sh.speaker, sh.expression, sh.action,
                    scene.motion_prompt, scene.visual_description, sh.index,
                    settings.VIDEO_CAMERA_MOTION,
                    speech=speech, voice_desc=voice_desc, sound_design=getattr(scene, "sound_design", None),
                    model_voice=model_audio,
                )
                seed = _seed(project_id, scene.scene_number, sh.index, "clip")

            # A finished clip is reused only if it was made from EXACTLY this request. (Reusing by shot number alone
            # would put the wrong picture/speech in a slot whenever the shot plan shifts.) Clips from older versions
            # have no key file and are kept as they are.
            clip_key = hashlib.sha1(
                f"{mode}|{model_audio}|{prompt}|{request_len:.2f}|{seed}|{os.path.basename(source or '')}".encode()
            ).hexdigest()
            key_file = raw + ".key"
            stored_key = None
            if os.path.exists(key_file):
                try:
                    stored_key = open(key_file).read().strip()
                except OSError:
                    stored_key = None
            if may_reuse and _nonempty(raw) and (stored_key is None or stored_key == clip_key):
                reused += 1
            else:
                if t2v:
                    if model_audio:
                        ctx.call(self.video.generate_text_clip, prompt, raw, request_len, seed, True)
                    else:
                        ctx.call(self.video.generate_text_clip, prompt, raw, request_len, seed)
                elif model_audio:
                    ctx.call(self.video.generate_clip, source, raw, prompt, request_len, seed, True)
                else:
                    ctx.call(self.video.generate_clip, source, raw, prompt, request_len, seed)
                with open(key_file, "w") as fh:
                    fh.write(clip_key)
            if t2v and sh.index == 0 and not _nonempty(wide_path):
                # Thumbnail for the gallery: a frame from the first generated clip.
                ctx.call(self.ff.extract_frame, raw, wide_path)
                ctx.set_scene_image(wide_path)
            if model_audio:
                # Keep the clip's own length and its own voice/sound: no stretching, so lips stay in sync.
                ctx.call(self.ff.normalize_clip, raw, fit, W, H, FPS, settings.OUTPUT_SHARPEN, settings.VIDEO_INTERPOLATE)
            else:
                ctx.call(self.ff.fit_clip, raw, fit, sh.duration, W, H, FPS, settings.OUTPUT_SHARPEN, settings.VIDEO_INTERPOLATE)
            fitted.append(fit)
            if hybrid:
                # Every shot except the last also carries a short tail, so shots can be crossfaded together.
                tail = 0.0 if sh.index == len(shots) - 1 else AMBIENCE_CROSSFADE
                amb = os.path.join(scene_dir, f"shot_{sh.index}_amb.wav")
                ctx.call(self.ff.extract_ambience, raw, amb, sh.duration + tail)
                ambience_parts.append(amb)

        # ---------------------------------------------------------- 6. mix
        ctx.checkpoint()
        if model_audio:
            ctx.stage(STAGE_ASSEMBLE, 0.93, "Joining shots")
            self.ff.concatenate_videos(fitted, final_path, "aac")
            total = self.ff.get_duration(final_path)
            ctx.stage(STAGE_ASSEMBLE, 1.0, "Scene done")
            return SceneResult(final_path, wide_path, total, len(shots), reused)

        ctx.stage(STAGE_ASSEMBLE, 0.93, "Mixing audio and picture")
        wav = os.path.join(scene_dir, "voices.wav")
        self.ff.build_audio_track(
            [s.audio_path for s in segments], [s.gap_after for s in segments], wav, lead_in=LEAD_IN,
        )
        mixed_wav = wav
        if hybrid and ambience_parts:
            amb_all = os.path.join(scene_dir, "ambience.wav")
            self.ff.join_ambience(ambience_parts, AMBIENCE_CROSSFADE, amb_all)
            mixed_wav = os.path.join(scene_dir, "voices_and_ambience.wav")
            self.ff.mix_voices_with_ambience(wav, amb_all, mixed_wav, settings.AMBIENCE_VOLUME)
        self.ff.assemble_scene(fitted, mixed_wav, final_path)
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
