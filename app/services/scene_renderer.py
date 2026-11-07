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
import zlib
import re
import shutil
import dataclasses
from collections import deque
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
    limit_sentences,
    speech_seconds,
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

    def _fingerprint(self, scene, characters: Sequence, style: str, cast: VoiceCast, video_mode: str, narrator: bool = False, scene_seconds=None, prev_sig: str = "", resolution=None, style_mode: str = "shots") -> str:
        reuse = style_mode == "reuse"
        blob = json.dumps(
            {
                "v": 9,
                "img": scene.image_prompt,
                "motion": scene.motion_prompt,
                "vis": scene.visual_description,
                "loc": scene.location,
                "place": [getattr(scene, "place_key", ""), getattr(scene, "place_text", "")],
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
                "narrator": bool(narrator),
                "continues": prev_sig,
                "scenemode": [style_mode, float(scene_seconds or settings.WHOLE_SCENE_MAX_SECONDS)],
                "audio": [settings.AUDIO_MODE, settings.AMBIENCE_VOLUME, getattr(scene, "sound_design", None), settings.MODEL_VOICE_MAX_WORDS],
                "t2vres": resolution or settings.VIDEO_T2V_RESOLUTION,
                "reuse": [bool(reuse), settings.REUSE_BASE_SECONDS] if reuse else False,
                "speech": [settings.MODEL_VOICE_WPS, settings.VIDEO_MAX_CLIP_SECONDS, settings.SPEECH_COMPLETE_CHECK,
                           settings.SPEECH_RETRY_EXTRA_SECONDS, settings.SPEECH_MAX_RETRIES, settings.SPEECH_TAIL_MARGIN_DB],
                "driven": settings.VIDEO_AUDIO_DRIVEN,
                "staging": [settings.CONVERSATION_STAGING, "sit2"],
                "complete": ["whole-split", settings.SPEECH_FALLBACK_TTS],
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

    def _segments(self, scene, cast: VoiceCast, model_audio: bool = False, narrator: bool = False, whole: Optional[bool] = None) -> List[Segment]:
        # Each line is first split into ACTION (performed on screen) and WORDS (the only thing ever spoken).
        turns = []
        for raw in (scene.dialogue_turns or []):
            t = normalize_turn(raw)
            if clean_for_tts(t.get("text", ""), t.get("speaker", "")):
                turns.append(t)
        narrator_on = bool(narrator)
        if not narrator_on:
            # Characters only: nothing is spoken by a narrator, not even lines the script labelled "Narrator".
            turns = [t for t in turns if not is_narrator((t.get("speaker") or "").strip())]
        narration = clean_for_tts(scene.narration_text or "") if narrator_on else ""
        if whole is None:
            whole = (settings.SCENE_MODE or "shots").strip().lower() == "whole"
        chunk_words = 60 if whole else self._max_words()   # whole scene: lines stay whole, long ones are split into more clips later
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
            add(NARRATOR_NAME, "narration", limit_sentences(narration, n_words))
        for t in turns:
            speaker = (t.get("speaker") or "").strip() or "Character"
            if is_narrator(speaker):
                kind, speaker = "narration", NARRATOR_NAME
            else:
                kind = "dialogue"
            add(speaker, kind, limit_sentences(clean_for_tts(t.get("text", ""), speaker), 60),
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
    # ------------------------------------------------------------- clip sizing
    def _clip_limit(self) -> float:
        """The longest clip the video server really makes: the smaller of VIDEO_MAX_CLIP_SECONDS and what the server
        itself reports (LTX_MAX_FRAMES). A line is planned to fit in this - never longer than the server can speak."""
        lim = float(settings.VIDEO_MAX_CLIP_SECONDS)
        srv = getattr(self.video, "server_max_seconds", lambda: None)()
        if srv:
            lim = min(lim, float(srv))
        return lim

    def _retry_limit(self, extra: float) -> float:
        """How long a retry may ask for: the server's real limit when it reports one; otherwise a little beyond
        VIDEO_MAX_CLIP_SECONDS (the server may allow more than the planning cap - a clip that does not get longer is
        noticed after the first retry)."""
        srv = getattr(self.video, "server_max_seconds", lambda: None)()
        if srv:
            return float(srv)
        return float(settings.VIDEO_MAX_CLIP_SECONDS) + extra * max(1, int(settings.SPEECH_MAX_RETRIES))

    def _max_words(self) -> int:
        """Most words one clip can carry: room for the speech at the expected pace plus a lead-in and a tail."""
        wps = float(settings.MODEL_VOICE_WPS) or 2.1
        fit = int(max(0.0, self._clip_limit() - 1.8) * wps)
        return max(4, min(int(settings.MODEL_VOICE_MAX_WORDS), fit))

    @staticmethod
    def _talk_seconds(part: Sequence[Segment]) -> float:
        return sum(speech_seconds(sg.text) + 0.45 for sg in part if sg.kind != "ambient")

    @staticmethod
    def _fits(talk: float, cap: float) -> bool:
        """Does this much speech fit in ONE clip of `cap` seconds? (Lenient: the clip is asked for generously and a line
        that still overruns is caught by the completion check, so lines are not split before they have to be.)"""
        return talk * 1.05 + 0.8 <= cap

    @staticmethod
    def _request_len(talk: float, cap: float) -> float:
        """Clip length to ask for: the speech plus 15 % (voices run slower than estimated) plus a lead-in. If the line
        still is not finished the clip is generated again, longer, or the scene is split (see _render_whole)."""
        return min(max(talk * 1.15 + 1.2, 3.0), cap)

    def _split_text(self, seg: Segment, parts_wanted: int = 2) -> List[Segment]:
        """Splits ONE line into complete pieces at sentence / clause ends (never mid-phrase)."""
        words = len(seg.text.split())
        if words < 6:
            return [seg]
        pieces = chunk_speech(seg.text, max(3, -(-words // parts_wanted)))
        if len(pieces) < 2:
            return [seg]
        out = []
        for n, piece in enumerate(pieces):
            out.append(dataclasses.replace(seg, text=piece, action=seg.action if n == 0 else "",
                                           duration=round(speech_seconds(piece) + 0.6, 2)))
            out[-1].splits = getattr(seg, "splits", 0) + 1
        return out

    def _whole_parts(self, segments: List[Segment], cap: float) -> List[List[Segment]]:
        """Groups consecutive lines into the fewest pieces that each fit in ONE clip of `cap` seconds. A line is never
        shortened: one that cannot fit on its own is split at sentence / clause ends into several clips."""
        spoken: List[Segment] = []
        for seg in segments:
            if seg.kind == "ambient":
                continue
            while not self._fits(self._talk_seconds([seg]), cap) and len(seg.text.split()) >= 6 \
                    and getattr(seg, "splits", 0) < 3:
                pieces = self._split_text(seg)
                if len(pieces) < 2:
                    break
                spoken.extend(pieces[:-1])
                seg = pieces[-1]
            spoken.append(seg)
        parts: List[List[Segment]] = []
        cur: List[Segment] = []
        for seg in spoken:
            if cur and not self._fits(self._talk_seconds(cur + [seg]), cap):
                parts.append(cur)
                cur = []
            cur.append(seg)
        if cur:
            parts.append(cur)
        return parts

    def _split_part(self, part: List[Segment], queue: "deque") -> bool:
        """A clip whose last line is still cut off after the retries: give the last line its own, shorter clip
        (several lines), or cut a single long line into two complete pieces. True if the queue was changed."""
        if len(part) > 1:
            queue.appendleft(part[-1:])
            queue.appendleft(part[:-1])
            return True
        seg = part[0]
        if getattr(seg, "splits", 0) >= 3:
            return False
        pieces = self._split_text(seg)
        if len(pieces) < 2:
            return False
        for piece in reversed(pieces):
            queue.appendleft([piece])
        return True

    @staticmethod
    def _place(scene) -> str:
        """The place as the story bible describes it (same words every time it comes back); else the scene's own text."""
        return getattr(scene, "place_text", None) or scene.location or ""

    def _dub_with_tts(self, ctx, raw: str, seg: Segment, cast: VoiceCast) -> None:
        """Last resort for a line the video model keeps cutting off: the pipeline's own voice says the WHOLE line over
        the clip (the picture is held on its last frame if the voice runs longer). Lips will not match, meaning does."""
        wav = raw + ".line.mp3"
        dubbed = raw + ".dub.mp4"
        profile = cast.profile(seg.speaker, seg.expression, seg.action, seg.text)
        ctx.call(self.tts.synthesize, seg.text, profile, wav, seg.speaker)
        ctx.call(self.ff.dub_clip, raw, wav, dubbed)
        os.replace(dubbed, raw)
        try:
            os.remove(wav)
        except OSError:
            pass
        logger.warning("The video model kept cutting off \"%s\" - the line is now spoken by the pipeline's own voice so "
                       "it is heard in full.", seg.text[:60])

    def _render_whole(self, scene, characters, style, cast, scene_dir, project_id, ctx, segments, mode,
                      on_screen, may_reuse, final_path, scene_seconds=None, prev_clip=None, resolution=None):
        """One clip per scene (or per `WHOLE_SCENE_MAX_SECONDS` piece of a long one). Returns None when the server
        could only render a fraction of the requested length, so the caller can use the shot-by-shot path."""
        W, H, FPS = settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT, settings.VIDEO_FPS
        cap = float(scene_seconds or settings.WHOLE_SCENE_MAX_SECONDS)   # the project's own choice wins over .env
        server_cap = getattr(self.video, "server_max_seconds", lambda: None)()
        if server_cap and server_cap < cap:
            logger.warning("Scene %s: the video server only makes %.1f s clips (LTX_MAX_FRAMES), below "
                           "the %.0f s you asked for. Raise LTX_MAX_FRAMES in the Kaggle launch cell for longer scenes.",
                           scene.scene_number, server_cap, cap)
            cap = server_cap
        parts = self._whole_parts(segments, cap) or [[]]
        if len(parts) > 1:
            logger.info("Scene %s: the dialogue needs %d clips of up to %.1f s (nothing is shortened).",
                        scene.scene_number, len(parts), cap)
        t2v = mode == "t2v"
        wide_path = os.path.join(scene_dir, "keyframe.png")
        ctx.stage(STAGE_AUDIO, 0.05, "Preparing the scene")
        source = None
        continuing = bool(prev_clip)
        if continuing:
            # Carry on from the previous scene: its last frame is the first picture (works in both modes, no image model).
            ctx.stage(STAGE_IMAGES, 0.10, "Carrying on from the previous scene")
            start_frame = os.path.join(scene_dir, "start_frame.png")
            if not (may_reuse and _nonempty(start_frame)):
                ctx.call(self.ff.extract_last_frame, prev_clip, start_frame)
            wide_path = start_frame
            ctx.set_scene_image(start_frame)
            source = start_frame
        elif not t2v:
            ctx.stage(STAGE_IMAGES, 0.10, "Drawing the scene")
            if not (may_reuse and _nonempty(wide_path)):
                ctx.checkpoint()
                ctx.call(
                    self.image.generate_image,
                    pb.keyframe_prompt(scene.image_prompt or scene.visual_description, on_screen, style, getattr(scene, "place_desc", "")),
                    wide_path, pb.image_negative(style), None, None, _seed(project_id, getattr(scene, "place_key", "") or scene.scene_number, "wide"),
                )
            ctx.set_scene_image(wide_path)
            source = wide_path
        fitted: List[str] = []
        reused = 0
        queue = deque(parts)
        planned = len(parts)
        n = 0
        chain_sig = ""
        while queue:
            part = queue.popleft()
            ctx.checkpoint()
            planned = max(planned, n + 1 + len(queue))
            ctx.stage(STAGE_VIDEOS, 0.35 + 0.55 * n / planned, f"Generating the scene ({n + 1}/{planned})")
            beats = []
            for seg in part:
                member = self._character_for(seg.speaker, cast, characters) if seg.kind == "dialogue" else None
                beats.append({
                    "kind": seg.kind, "speaker": seg.speaker, "expression": seg.expression, "action": seg.action,
                    "speech": seg.text, "voice_desc": cast.voice_description(seg.speaker),
                })
            talk = self._talk_seconds(part)
            request_len = self._request_len(talk, cap)
            # A later piece of the scene carries on from the last picture of the piece before it.
            part_source = source
            if n > 0 and fitted:
                part_source = os.path.join(scene_dir, f"whole_{n}_start.png")
                ctx.call(self.ff.extract_last_frame, fitted[-1], part_source)
            prompt = pb.whole_scene_prompt(
                beats, on_screen, self._place(scene), scene.visual_description, style, scene.motion_prompt,
                getattr(scene, "sound_design", None), start_image=bool(continuing or (n > 0 and part_source)),
            )
            seed = _seed(project_id, scene.scene_number, "whole", n)
            raw = os.path.join(scene_dir, f"whole_{n}_raw.mp4")
            fit = os.path.join(scene_dir, f"whole_{n}.mp4")
            key = hashlib.sha1(
                f"whole|{mode}|{prompt}|{request_len:.2f}|{seed}|{os.path.basename(part_source or '')}|{chain_sig}".encode()
            ).hexdigest()
            key_file = raw + ".key"
            stored = open(key_file).read().strip() if os.path.exists(key_file) else None

            def render(length: float, shift: int = 0, _prompt=prompt, _seed0=seed, _src=part_source, _raw=raw):
                seed_now = _seed0 + shift
                if t2v and _src is None:
                    ctx.call(self.video.generate_text_clip, _prompt, _raw, length, seed_now, True, resolution=resolution)
                else:
                    ctx.call(self.video.generate_clip, _src, _raw, _prompt, length, seed_now, True, resolution=resolution)

            if may_reuse and _nonempty(raw) and stored == key:
                reused += 1
            else:
                render(request_len)
                got = self.ff.get_duration(raw)
                if got < 0.8 * request_len:
                    if settings.WHOLE_SCENE_FALLBACK:
                        for f in (raw, key_file):
                            if os.path.exists(f):
                                os.remove(f)
                        return None
                    logger.warning("Scene %s: asked for %.1f s but the server returned %.1f s (GPU memory). Keeping it; "
                                   "lower VIDEO_T2V_RESOLUTION or WHOLE_SCENE_MAX_SECONDS.", scene.scene_number, request_len, got)
                # Was the last line finished? If not: generate again, longer; then give it a clip of its own; then
                # (last resort) say it with the pipeline's own voice. A line is never left cut off.
                if settings.SPEECH_COMPLETE_CHECK and any(sg.text for sg in part):
                    complete = self._finish_the_line(ctx, raw, request_len, render, n, "Scene part")
                    if not complete:
                        if self._split_part(part, queue):
                            logger.warning("Scene %s: the last line did not fit, so the scene is split into more clips "
                                           "(nothing is cut).", scene.scene_number)
                            for f in (raw, key_file):
                                if os.path.exists(f):
                                    os.remove(f)
                            continue
                        if settings.SPEECH_FALLBACK_TTS and len(part) == 1:
                            self._dub_with_tts(ctx, raw, part[0], cast)
                with open(key_file, "w") as fh:
                    fh.write(key)
            if n == 0 and t2v and not continuing and not _nonempty(wide_path):
                ctx.call(self.ff.extract_frame, raw, wide_path)
                ctx.set_scene_image(wide_path)
            ctx.call(self.ff.normalize_clip, raw, fit, W, H, FPS, settings.OUTPUT_SHARPEN, settings.VIDEO_INTERPOLATE)
            fitted.append(fit)
            chain_sig = key
            n += 1
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
        narrator: Optional[bool] = None,
        scene_seconds: Optional[int] = None,
        prev_clip: Optional[str] = None,
        resolution: Optional[str] = None,
        scene_style: Optional[str] = None,
    ) -> SceneResult:
        """video_mode: "i2v" (draw a keyframe with the image model, then animate it) or "t2v" (text-to-video, no
        image model). None = the server-wide default (settings.VIDEO_MODE)."""
        W, H, FPS = settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT, settings.VIDEO_FPS
        reused = 0
        mode = (video_mode or settings.VIDEO_MODE or "i2v").strip().lower()
        mode = "t2v" if mode == "t2v" else "i2v"
        style_mode = (scene_style or settings.SCENE_MODE or "shots").strip().lower()   # per-project choice wins
        style_mode = style_mode if style_mode in ("whole", "shots", "reuse", "film") else "shots"
        whole_mode = style_mode in ("whole", "film")   # "film" = whole scenes that carry on from one another
        reuse = style_mode == "reuse"
        # Reused footage has no speech of its own, so the voices are always recorded by text-to-speech.
        model_audio = (settings.AUDIO_MODE or "voices").strip().lower() == "model" and not reuse
        free_cam = pb.is_free_camera(settings.VIDEO_CAMERA_MOTION)
        narrator = bool(settings.NARRATOR_ENABLED if narrator is None else narrator)   # per-project choice wins

        # A scene that carries on from the previous one starts from that clip's last picture (same people, same place).
        prev_clip = prev_clip if (getattr(scene, "continues_previous", False) and prev_clip and os.path.exists(prev_clip)) else None
        prev_sig = f"{os.path.getsize(prev_clip)}:{int(os.path.getmtime(prev_clip))}" if prev_clip else ""
        fp = self._fingerprint(scene, characters, style, cast, mode, narrator, scene_seconds, prev_sig, resolution, style_mode)
        may_reuse = self._prepare_dir(scene_dir, fp)
        final_path = os.path.join(scene_dir, "scene_final.mp4")
        if may_reuse and _nonempty(final_path):
            wide = os.path.join(scene_dir, "keyframe.png")
            total = self.ff.get_duration(final_path)
            return SceneResult(final_path, wide, total, 0, 0)

        on_screen = self._characters_on_screen(scene, characters)
        segments = self._segments(scene, cast, model_audio, narrator, whole=whole_mode)

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
                    profile = cast.profile(seg.speaker, seg.expression, seg.action, seg.text)
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

        if reuse:
            return self._render_reuse(scene, characters, style, cast, scene_dir, project_id, ctx, segments, shots, total,
                                      mode, on_screen, scene_seconds, prev_clip, prev_sig, resolution, final_path, reused)

        if model_audio and whole_mode:
            whole = self._render_whole(scene, characters, style, cast, scene_dir, project_id, ctx,
                                       segments, mode, on_screen, may_reuse, final_path, scene_seconds, prev_clip, resolution)
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
                    pb.keyframe_prompt(scene.image_prompt or scene.visual_description, on_screen, style, getattr(scene, "place_desc", "")),
                    wide_path, pb.image_negative(style), None, None, _seed(project_id, getattr(scene, "place_key", "") or scene.scene_number, "wide"),
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
                            pb.closeup_prompt(member, expression, self._place(scene), style, free_cam),
                            path, pb.image_negative(style), None, None, _seed(project_id, member.name, "face"),
                        )
                    keyframe_for_speaker[name] = path
            ctx.stage(STAGE_IMAGES, 0.35, "Keyframes ready")


        # Audio-driven models (MultiTalk / InfiniteTalk / Hunyuan Avatar) animate a face to AUDIO: build the scene's voice track
        # first so every shot can be given its own slice of it.
        audio_driven = bool(settings.VIDEO_AUDIO_DRIVEN) and not model_audio
        voices_wav = None
        if audio_driven:
            if t2v:
                raise RuntimeError(
                    "VIDEO_AUDIO_DRIVEN=true needs an image-first (i2v) project: the audio-driven video models animate a "
                    "face from a picture. Switch the project to image-first, or set VIDEO_AUDIO_DRIVEN=false."
                )
            voices_wav = os.path.join(scene_dir, "voices.wav")
            ctx.call(self.ff.build_audio_track, [x.audio_path for x in segments], [x.gap_after for x in segments],
                     voices_wav, True, LEAD_IN)

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
            request_len = min(sh.duration + (0.8 if model_audio else 0.4), self._clip_limit())
            shot_audio = None
            if audio_driven:
                # The clip is exactly as long as the slice of voice it is animated to.
                shot_audio = os.path.join(scene_dir, f"shot_{sh.index}_audio.wav")
                ctx.call(self.ff.cut_audio, voices_wav, sh.start, sh.duration, shot_audio)
                request_len = max(1.0, sh.duration)
            if t2v:
                member = self._character_for(sh.speaker, cast, characters) if sh.kind == "dialogue" else None
                prompt = pb.text_video_prompt(
                    sh.kind, sh.speaker, sh.expression, sh.action,
                    getattr(member, "appearance_prompt", "") or getattr(member, "description", "") if member else "",
                    on_screen, self._place(scene), scene.visual_description, style, sh.index,
                    settings.VIDEO_CAMERA_MOTION, getattr(scene, "sound_design", None), hybrid,
                    scene_motion=scene.motion_prompt, speech=speech, voice_desc=voice_desc, model_voice=model_audio,
                    dialogue_text=sh.text if sh.kind == "dialogue" else "",
                )
                # Same seed for the same character = the best-effort way to keep a similar face between shots.
                base_seed = _seed(project_id, sh.speaker, "face") if sh.kind == "dialogue" else _seed(project_id, getattr(scene, "place_key", "") or scene.scene_number, "wide")
                source = None
            else:
                source = keyframe_for_speaker.get(sh.speaker) or wide_path
                prompt = pb.video_prompt(
                    sh.kind, sh.speaker, sh.expression, sh.action,
                    scene.motion_prompt, scene.visual_description, sh.index,
                    settings.VIDEO_CAMERA_MOTION,
                    speech=speech, voice_desc=voice_desc, sound_design=getattr(scene, "sound_design", None),
                    model_voice=model_audio, dialogue_text=sh.text if sh.kind == "dialogue" else "",
                )
                base_seed = _seed(project_id, scene.scene_number, sh.index, "clip")

            # A finished clip is reused only if it was made from EXACTLY this request. (Reusing by shot number alone
            # would put the wrong picture/speech in a slot whenever the shot plan shifts.) Clips from older versions
            # have no key file and are kept as they are.
            clip_key = hashlib.sha1(
                f"{mode}|{model_audio}|{audio_driven}|{prompt}|{request_len:.2f}|{base_seed}|{os.path.basename(source or '')}".encode()
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
                def render(length: float, shift: int = 0, _t2v=t2v, _prompt=prompt, _seed0=base_seed, _src=source,
                           _aud=shot_audio, _raw=raw):
                    seed_now = _seed0 + shift
                    if _t2v:
                        if model_audio:
                            ctx.call(self.video.generate_text_clip, _prompt, _raw, length, seed_now, True, resolution=resolution)
                        else:
                            ctx.call(self.video.generate_text_clip, _prompt, _raw, length, seed_now, resolution=resolution)
                    elif _aud:
                        ctx.call(self.video.generate_clip, _src, _raw, _prompt, length, seed_now, False,
                                 resolution=resolution, audio_path=_aud)
                    elif model_audio:
                        ctx.call(self.video.generate_clip, _src, _raw, _prompt, length, seed_now, True, resolution=resolution)
                    else:
                        ctx.call(self.video.generate_clip, _src, _raw, _prompt, length, seed_now, resolution=resolution)

                render(request_len)
                if model_audio and speech and settings.SPEECH_COMPLETE_CHECK:
                    if not self._finish_the_line(ctx, raw, request_len, render, sh.index) and settings.SPEECH_FALLBACK_TTS:
                        self._dub_with_tts(ctx, raw, sh, cast)
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
        if not (voices_wav and _nonempty(wav)):
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

    # ------------------------------------------------------------ reused footage
    def _render_reuse(self, scene, characters, style, cast, scene_dir, project_id, ctx, segments, shots, total, mode,
                      on_screen, scene_seconds, prev_clip, prev_sig, resolution, final_path, reused):
        """ONE short clip per scene; every shot is cut from it (own start point, zoom and side) and the voices recorded
        by text-to-speech are laid on top. The clip is kept in a folder that survives edits to the dialogue, so
        changing or adding lines only records voices again - the footage is not generated a second time."""
        W, H, FPS = settings.OUTPUT_WIDTH, settings.OUTPUT_HEIGHT, settings.VIDEO_FPS
        t2v = mode == "t2v"
        cap = float(scene_seconds or settings.WHOLE_SCENE_MAX_SECONDS)
        server_cap = getattr(self.video, "server_max_seconds", lambda: None)()
        base_len = max(3.0, min(float(settings.REUSE_BASE_SECONDS), cap, float(server_cap or 1e9), total + 0.5))
        sound = getattr(scene, "sound_design", None)
        if t2v:
            prompt = pb.text_video_prompt("ambient", "", "", "", "", on_screen, self._place(scene), scene.visual_description,
                                          style, 0, settings.VIDEO_CAMERA_MOTION, sound, False, scene_motion=scene.motion_prompt)
        else:
            prompt = pb.video_prompt("ambient", "", "", "", scene.motion_prompt, scene.visual_description, 0,
                                     settings.VIDEO_CAMERA_MOTION, sound_design=sound)
        seed = _seed(project_id, scene.scene_number, "base")
        foot_dir = os.path.join(os.path.dirname(scene_dir), "_footage")
        os.makedirs(foot_dir, exist_ok=True)
        base = os.path.join(foot_dir, f"{os.path.basename(scene_dir)}_base.mp4")
        key_file = base + ".key"
        key = hashlib.sha1(json.dumps([
            "reuse", mode, prompt, round(base_len, 2), seed, resolution or settings.VIDEO_T2V_RESOLUTION, prev_sig,
            None if t2v else (scene.image_prompt or scene.visual_description), style,
        ], default=str).encode()).hexdigest()
        stored = open(key_file).read().strip() if os.path.exists(key_file) else None
        wide_path = os.path.join(scene_dir, "keyframe.png")

        if _nonempty(base) and stored == key:
            reused += 1
            ctx.stage(STAGE_VIDEOS, 0.5, "Using the footage already made for this scene")
        else:
            source = None
            if prev_clip:
                ctx.stage(STAGE_IMAGES, 0.10, "Carrying on from the previous scene")
                source = os.path.join(scene_dir, "start_frame.png")
                ctx.call(self.ff.extract_last_frame, prev_clip, source)
            elif not t2v:
                ctx.stage(STAGE_IMAGES, 0.10, "Drawing the scene")
                ctx.checkpoint()
                ctx.call(self.image.generate_image,
                         pb.keyframe_prompt(scene.image_prompt or scene.visual_description, on_screen, style, getattr(scene, "place_desc", "")),
                         wide_path, pb.image_negative(style), None, None, _seed(project_id, getattr(scene, "place_key", "") or scene.scene_number, "wide"))
                source = wide_path
            ctx.checkpoint()
            ctx.stage(STAGE_VIDEOS, 0.40, "Generating the scene footage (used for every shot)")
            raw = base + ".tmp.mp4"
            if source is None:
                ctx.call(self.video.generate_text_clip, prompt, raw, base_len, seed, False, resolution=resolution)
            else:
                ctx.call(self.video.generate_clip, source, raw, prompt, base_len, seed, False, resolution=resolution)
            os.replace(raw, base)
            with open(key_file, "w") as fh:
                fh.write(key)

        if not _nonempty(wide_path):
            ctx.call(self.ff.extract_frame, base, wide_path)
        ctx.set_scene_image(wide_path)

        got = max(self.ff.get_duration(base), 1.0)
        loop = os.path.join(scene_dir, "base_loop.mp4")
        ctx.call(self.ff.pingpong, base, loop)
        fitted: List[str] = []
        for sh in shots:
            ctx.checkpoint()
            ctx.stage(STAGE_VIDEOS, 0.60 + 0.30 * sh.index / len(shots), f"Cutting shot {sh.index + 1}/{len(shots)}")
            fit = os.path.join(scene_dir, f"shot_{sh.index}.mp4")
            start = (sh.index * 0.618 * got) % (2 * got)           # a different part of the footage for each cut
            if sh.kind == "dialogue":
                side = 0.3 if (zlib.crc32((sh.speaker or "").encode()) % 2) else 0.7  # each speaker keeps to one side of the frame
                zoom, px = 1.28, side
            else:
                zoom, px = (1.0, 0.5) if sh.index % 2 == 0 else (1.12, 0.5)
            ctx.call(self.ff.cut_from_base, loop, fit, start, sh.duration, zoom, px, 0.45, W, H, FPS, settings.OUTPUT_SHARPEN)
            fitted.append(fit)

        ctx.checkpoint()
        ctx.stage(STAGE_ASSEMBLE, 0.93, "Mixing voices and picture")
        wav = os.path.join(scene_dir, "voices.wav")
        self.ff.build_audio_track([s.audio_path for s in segments], [s.gap_after for s in segments], wav, lead_in=LEAD_IN)
        self.ff.assemble_scene(fitted, wav, final_path)
        ctx.stage(STAGE_ASSEMBLE, 1.0, "Scene done")
        return SceneResult(final_path, wide_path, total, len(shots), reused)

    # ------------------------------------------------------------------ cut-off lines
    def _finish_the_line(self, ctx, raw: str, length: float, render, shot_index: int, label: str = "Shot") -> bool:
        """Model-voice mode: a clip whose voice is still going at its very last instant had its line cut off. Render it
        again, longer (and with a fresh seed), up to settings.SPEECH_MAX_RETRIES times. Stops early if the server
        cannot make a longer clip. A line that still seems cut off after that is logged, never silently dropped."""
        extra = max(0.5, float(settings.SPEECH_RETRY_EXTRA_SECONDS))
        margin = float(settings.SPEECH_TAIL_MARGIN_DB)
        for attempt in range(1, max(0, int(settings.SPEECH_MAX_RETRIES)) + 1):
            ctx.checkpoint()
            active, overall, tail = self.ff.tail_speech_active(raw, margin_db=margin)
            if not active:
                return True
            before = self.ff.get_duration(raw)
            cap = getattr(self, "_clip_cap", None)      # learned earlier in this run: the server cannot go longer
            if cap and before >= cap - 0.3:
                logger.warning("%s %d: the line may be cut off, but the video server's clip-length limit (~%.1f s) is reached.",
                               label, shot_index + 1, cap)
                return False
            limit = self._retry_limit(extra)
            if length >= limit - 0.1:
                logger.warning("%s %d: the line may be cut off and this clip is already as long as the video server can make "
                               "(%.1f s) - not generating it again.", label, shot_index + 1, limit)
                return False
            length = min(length + extra, limit)
            logger.warning(
                "%s %d: the spoken line seems to be cut off (last 0.3 s at %.1f dB vs %.1f dB overall) - "
                "generating it again at %.1f s (try %d of %d).",
                label, shot_index + 1, tail, overall, length, attempt, int(settings.SPEECH_MAX_RETRIES),
            )
            try:
                os.remove(raw)
            except OSError:
                pass
            render(length, attempt)
            if self.ff.get_duration(raw) < before + 0.4:
                self._clip_cap = max(before, self.ff.get_duration(raw))
                logger.warning("%s %d: the video server did not make a longer clip (its clip-length limit?).",
                               label, shot_index + 1)
                return not self.ff.tail_speech_active(raw, margin_db=margin)[0]
        active, overall, tail = self.ff.tail_speech_active(raw, margin_db=margin)
        if active:
            logger.warning("%s %d: the line may still be cut off after %d retries.", label, shot_index + 1,
                           int(settings.SPEECH_MAX_RETRIES))
        return not active

    # ------------------------------------------------------------------ misc
    def _silent_mp3(self, path: str, seconds: float) -> None:
        import subprocess

        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.2f}",
             "-codec:a", "libmp3lame", "-q:a", "9", path],
            check=True, capture_output=True,
        )
