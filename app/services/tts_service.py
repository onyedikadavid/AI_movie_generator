"""
Text-to-speech for dialogue and narration.

Engine order (first one that works wins):
  1. edge-tts  - Microsoft neural voices, free, no API key. Natural-sounding,
                 with real male/female voices and Nigerian-English voices.
  2. Piper     - local, only if PIPER_MODEL_MALE / PIPER_MODEL_FEMALE are set.
  3. gTTS      - Google Translate voice (robotic). Pitch-shifted per gender so
                 characters at least don't all sound identical.
  4. silence   - so a run can still finish with no TTS available at all.

Steps 2-4 are only used if TTS_ALLOW_FALLBACK=true. By default the run fails
with a clear message instead (and can be resumed), because silently shipping
the old robotic voice is exactly the problem this module exists to solve.
"""
import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import time
from typing import List, Set

from app.core.config import settings
from app.services.voice_service import FALLBACK_VOICE, VoiceProfile, clean_for_tts

logger = logging.getLogger(__name__)

_MIN_AUDIO_BYTES = 400


class TTSError(RuntimeError):
    pass


def _rate(n: int) -> str:
    return f"{int(n):+d}%"


def _pitch(n: int) -> str:
    return f"{int(n):+d}Hz"


def _ok(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) >= _MIN_AUDIO_BYTES


def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, **kwargs)


class TTSService:
    def __init__(self):
        # Human-readable notes about anything that fell short of the best voice
        # quality; the pipeline copies these into project.warning_message.
        self.warnings: List[str] = []
        self.engines_used: Set[str] = set()

    # ------------------------------------------------------------------ public
    def synthesize(self, text: str, profile: VoiceProfile, output_path: str, speaker: str = "") -> str:
        """Speak `text` in `profile`'s voice and write an mp3 to `output_path`."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        spoken = clean_for_tts(text, speaker=speaker)
        if not spoken:
            return self._silence(output_path, 0.8)

        engine = (settings.TTS_ENGINE or "edge").strip().lower()
        errors: List[str] = []

        if engine == "edge":
            try:
                self._edge(spoken, profile, output_path)
                self.engines_used.add("edge-tts")
                return output_path
            except Exception as e:  # noqa: BLE001 - we want to report any failure reason
                errors.append(f"edge-tts: {e}")
                if not settings.TTS_ALLOW_FALLBACK:
                    raise TTSError(
                        "Couldn't generate the neural voice for "
                        f"{profile.label or 'a line'} ({e}). "
                        "Check that the worker machine has internet access and that the "
                        "'edge-tts' package is installed (pip install edge-tts). Everything "
                        "finished so far is kept - press Resume to continue. To use a basic "
                        "robotic fallback voice instead, set TTS_ALLOW_FALLBACK=true."
                    ) from e
                logger.warning("edge-tts failed (%s) - falling back (TTS_ALLOW_FALLBACK is on).", e)

        # ---- non-neural engines: only reached if the person chose one of them
        #      as TTS_ENGINE, or explicitly allowed falling back to them.
        order = ["gtts"] if engine == "gtts" else ["piper", "gtts"]
        for name in order:
            fn = self._piper if name == "piper" else self._gtts
            try:
                fn(spoken, profile, output_path)
                self.engines_used.add(name)
                if engine == "edge":  # we only got here because edge-tts failed
                    self._warn_degraded(name, errors)
                return output_path
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")

        logger.error("All TTS engines failed (%s) - writing silence.", "; ".join(errors))
        self.warnings.append("No text-to-speech engine worked, so some lines are silent.")
        return self._silence(output_path, max(1.0, min(len(spoken.split()) / 2.6, 30.0)))

    # Backwards compatible helper (older callers).
    def generate_speech(self, text: str, output_path: str) -> str:
        from app.services.voice_service import narrator_profile

        return self.synthesize(text, narrator_profile("us", settings.TTS_NARRATOR_VOICE), output_path)

    # ------------------------------------------------------------------ engines
    def _edge(self, text: str, profile: VoiceProfile, output_path: str) -> None:
        try:
            import edge_tts
        except ImportError as e:
            raise TTSError("the 'edge-tts' package is not installed") from e

        voices = [profile.voice]
        if FALLBACK_VOICE.get(profile.gender) not in voices:
            voices.append(FALLBACK_VOICE.get(profile.gender, "en-US-GuyNeural"))

        tmp = f"{output_path}.part"
        last_error: Exception = TTSError("unknown error")

        for voice in voices:
            for attempt in range(1, 4):
                try:
                    async def _go() -> None:
                        comm = edge_tts.Communicate(
                            text,
                            voice,
                            rate=_rate(profile.rate),
                            pitch=_pitch(profile.pitch),
                            volume=_rate(profile.volume),
                        )
                        await asyncio.wait_for(comm.save(tmp), timeout=60)

                    asyncio.run(_go())
                    if not _ok(tmp):
                        raise TTSError("the voice service returned an empty audio file")
                    os.replace(tmp, output_path)
                    if voice != profile.voice:
                        self.warnings.append(
                            f"Voice '{profile.voice}' was unavailable; '{voice}' was used instead."
                        )
                    return
                except Exception as e:  # noqa: BLE001
                    last_error = e
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                    # NoAudioReceived normally means "this voice name doesn't exist" -
                    # no point retrying it, move on to the fallback voice.
                    if type(e).__name__ == "NoAudioReceived":
                        break
                    if attempt < 3:
                        time.sleep(1.5 * attempt)
        raise last_error

    def _piper(self, text: str, profile: VoiceProfile, output_path: str) -> None:
        model = settings.PIPER_MODEL_MALE if profile.gender == "male" else settings.PIPER_MODEL_FEMALE
        model = model or settings.PIPER_MODEL_FEMALE or settings.PIPER_MODEL_MALE
        if not model or not shutil.which("piper"):
            raise TTSError("piper isn't configured")
        wav = f"{output_path}.piper.wav"
        proc = subprocess.run(
            ["piper", "--model", model, "--output_file", wav],
            input=text, text=True, capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not _ok(wav):
            raise TTSError(f"piper failed ({proc.returncode}): {proc.stderr[:200]}")
        _run(["ffmpeg", "-y", "-i", wav, "-codec:a", "libmp3lame", "-q:a", "3", output_path])
        os.remove(wav)

    def _gtts(self, text: str, profile: VoiceProfile, output_path: str) -> None:
        from gtts import gTTS

        raw = f"{output_path}.gtts.mp3"
        gTTS(text=text, lang="en").save(raw)
        if not _ok(raw):
            raise TTSError("gTTS returned an empty file")
        # gTTS has a single voice. Shift its pitch per gender (and slightly per
        # character) so a conversation doesn't sound like one person.
        base = 0.86 if profile.gender == "male" else 1.12
        jitter = ((int(hashlib.sha1((profile.label or profile.voice).encode()).hexdigest(), 16) % 7) - 3) / 100.0
        f = max(0.75, min(1.25, base + jitter))
        try:
            sr_out = _run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                 "-of", "default=nw=1:nk=1", raw],
                text=True,
            ).stdout.strip()
            sr = int(sr_out)
            _run([
                "ffmpeg", "-y", "-i", raw,
                "-af", f"asetrate={int(sr * f)},aresample={sr},atempo={1.0 / f:.4f}",
                "-codec:a", "libmp3lame", "-q:a", "3", output_path,
            ])
            os.remove(raw)
        except Exception:  # noqa: BLE001 - pitch shift is cosmetic; keep the raw voice
            os.replace(raw, output_path)

    def _silence(self, output_path: str, seconds: float) -> str:
        _run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.2f}",
            "-codec:a", "libmp3lame", "-q:a", "9", output_path,
        ])
        return output_path

    # ------------------------------------------------------------------ misc
    def _warn_degraded(self, engine: str, errors: List[str]) -> None:
        msg = (
            f"Neural voices were unavailable, so the basic '{engine}' voice was used for some lines "
            f"({errors[0] if errors else 'edge-tts not selected'})."
        )
        if msg not in self.warnings:
            self.warnings.append(msg)
