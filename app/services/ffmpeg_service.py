import os
import subprocess
from typing import List

class FFmpegService:
    def combine_scene_assets(self, video_path: str, audio_path: str, output_path: str) -> str:
        """Merges a single video scene with its corresponding narration audio."""
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