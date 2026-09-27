import os
import shutil
import subprocess
from typing import List


def _require_ffmpeg():
    """
    Fails with a clear, actionable message if the ffmpeg binary isn't on
    PATH, instead of letting subprocess.run raise a bare
    FileNotFoundError / WinError 2 with no indication of what's actually
    missing. Checked lazily (not at import time) so importing this module
    never fails just because ffmpeg isn't installed yet.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg was not found on PATH. This project shells out to the ffmpeg "
            "CLI to combine audio/video and stitch scenes together - it's a "
            "separate program, not something 'pip install' can provide.\n"
            "- Windows: download a build from https://www.gyan.dev/ffmpeg/builds/ "
            "(the 'essentials' or 'full' release build - a plain .zip, not a "
            "package-manager install) and manually add its bin\\ folder (the one "
            "containing ffmpeg.exe) to your PATH environment variable, then open a "
            "NEW terminal and confirm with: ffmpeg -version. Prefer this direct-zip "
            "method over winget/choco on Windows - package managers can install CLI "
            "tools as 'App Execution Alias' stubs that behave inconsistently when "
            "launched from Python's subprocess module (low-level CreateProcess) "
            "rather than an interactive shell.\n"
            "- macOS: brew install ffmpeg\n"
            "- Linux: sudo apt-get install ffmpeg"
        )


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    """
    Runs an ffmpeg/ffprobe command and, on failure, raises a RuntimeError
    that actually includes the captured stderr - previously this was
    captured (stderr=subprocess.PIPE) but never surfaced anywhere, so every
    failure showed only a bare exit code with zero indication of what
    ffmpeg itself was complaining about.
    """
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        stderr_tail = (e.stderr or "").strip()[-2000:]  # ffmpeg's actual error is usually at the end
        raise RuntimeError(
            f"Command failed (exit code {e.returncode}): {' '.join(cmd)}\n"
            f"--- ffmpeg/ffprobe stderr ---\n{stderr_tail or '(no stderr captured)'}"
        ) from e


class FFmpegService:
    def combine_scene_assets(self, video_path: str, audio_path: str, output_path: str) -> str:
        """Merges a single video scene with its corresponding narration audio."""
        _require_ffmpeg()
        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-shortest',
            output_path
        ]
        _run(cmd)
        return output_path

    def concatenate_videos(self, video_paths: List[str], output_path: str) -> str:
        """Concatenates multiple video scenes into a single final movie."""
        _require_ffmpeg()
        list_file_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")

        with open(list_file_path, "w") as f:
            for path in video_paths:
                # ffmpeg's concat demuxer parses this file with its own
                # mini-language where backslash is an escape character -
                # a raw Windows path like C:\Users\...\scene_1\clip.mp4
                # can get misparsed. Forward slashes work fine on Windows
                # too (ffmpeg normalizes them), so always use those here
                # regardless of platform.
                safe_path = os.path.abspath(path).replace(os.sep, "/")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file_path,
            '-c', 'copy',
            output_path
        ]
        _run(cmd)

        if os.path.exists(list_file_path):
            os.remove(list_file_path)

        return output_path

    def get_audio_duration(self, audio_path: str) -> float:
        """
        Returns the duration of an audio file in seconds via ffprobe (ships
        alongside ffmpeg in the same download/install). Used by
        pipeline_tasks.py to size the generated video clip to match the
        narration/dialogue audio length.
        """
        if shutil.which("ffprobe") is None:
            raise RuntimeError(
                "ffprobe was not found on PATH. It ships in the same download/install "
                "as ffmpeg (same bin\\ folder on Windows) - if you just installed "
                "ffmpeg, make sure that folder actually contains ffprobe.exe too, "
                "and that you opened a new terminal after adding it to PATH."
            )
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path,
        ]
        result = _run(cmd)
        return float(result.stdout.strip())
