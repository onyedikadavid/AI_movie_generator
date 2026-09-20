import os
import logging
import subprocess

logger = logging.getLogger(__name__)


class TTSService:
    def generate_speech(self, text: str, output_path: str) -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        text = text or ""
        audio_generated = False

        # 1. Preferred: local Piper TTS via CLI, text piped through stdin
        #    (avoids shell=True + string interpolation, a shell-injection risk).
        try:
            process = subprocess.run(
                ["piper", "--model", "en_US-lessac-medium", "--output_file", output_path],
                input=text,
                text=True,
                capture_output=True,
                timeout=120,
            )
            if process.returncode == 0 and os.path.exists(output_path):
                audio_generated = True
            else:
                logger.warning(f"piper TTS failed (code {process.returncode}): {process.stderr[:300]}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.info(f"piper not available, trying gTTS instead: {e}")

        # 2. Fallback: gTTS (needs outbound internet access; used when piper
        #    isn't installed - e.g. the lightweight Render/Dockerfile image).
        if not audio_generated and text.strip():
            try:
                from gtts import gTTS

                gTTS(text=text, lang="en").save(output_path)
                audio_generated = os.path.exists(output_path)
            except Exception as e:
                logger.warning(f"gTTS fallback failed: {e}")

        # 3. Last resort: silent placeholder clip, so the pipeline can keep
        #    going even with no TTS engine reachable at all.
        if not audio_generated:
            duration = max(1.0, min(len(text.split()) / 2.5, 30.0)) if text else 3.0
            fallback_cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", str(duration), "-q:a", "9", "-acodec", "libmp3lame", output_path,
            ]
            subprocess.run(fallback_cmd, check=True, capture_output=True)

        return output_path
