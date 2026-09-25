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
            "(the 'essentials' or 'full' release build), extract it, and add its "
            "bin\\ folder (the one containing ffmpeg.exe) to your PATH environment "
            "variable, then open a NEW terminal (PATH changes don't apply to "
            "already-open terminals) and confirm with: ffmpeg -version\n"
            "- Or via a package manager: choco install ffmpeg (Chocolatey) or "
            "winget install ffmpeg\n"
            "- macOS: brew install ffmpeg\n"
            "- Linux: sudo apt-get install ffmpeg"
        )


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
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return output_path

    def concatenate_videos(self, video_paths: List[str], output_path: str) -> str:
        """Concatenates multiple video scenes into a single final movie."""
        _require_ffmpeg()
        list_file_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")

        with open(list_file_path, "w") as f:
            for path in video_paths:
                f.write(f"file '{os.path.abspath(path)}'\n")

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file_path,
            '-c', 'copy',
            output_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
