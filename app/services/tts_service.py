import os
import logging
import subprocess

logger = logging.getLogger(__name__)


class TTSService:
    def generate_speech(self, text: str, output_path: str) -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        text = text or ""

        # Uses lightweight local Piper TTS via CLI, text piped through stdin
        # (avoids shell=True + string interpolation, which is a shell-injection risk).
        try:
            process = subprocess.run(
                ["piper", "--model", "en_US-lessac-medium", "--output_file", output_path],
                input=text,
                text=True,
                capture_output=True,
                timeout=120,
            )
            if process.returncode != 0:
                logger.warning(f"piper TTS failed (code {process.returncode}): {process.stderr[:300]}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"piper TTS unavailable: {e}")
            process = None

        if (process is None or process.returncode != 0) and not os.path.exists(output_path):
            # Fallback: create a silent placeholder clip so the pipeline can keep going
            # even without a local TTS engine installed.
            duration = max(1.0, min(len(text.split()) / 2.5, 30.0)) if text else 3.0
            fallback_cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
                "-t", str(duration), "-q:a", "9", "-acodec", "libmp3lame", output_path,
            ]
            subprocess.run(fallback_cmd, check=True, capture_output=True)

        return output_path
