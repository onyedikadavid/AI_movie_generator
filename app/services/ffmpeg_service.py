import os
import shutil
import subprocess
from typing import List, Optional, Sequence


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
    that actually includes the captured stderr.
    """
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        stderr_tail = (e.stderr or "").strip()[-2000:]  # ffmpeg's actual error is usually at the end
        raise RuntimeError(
            f"Command failed (exit code {e.returncode}): {' '.join(cmd)}\n"
            f"--- ffmpeg/ffprobe stderr ---\n{stderr_tail or '(no stderr captured)'}"
        ) from e


def _fwd(path: str) -> str:
    """ffmpeg's concat-list parser treats backslashes as escapes; forward
    slashes work on Windows too."""
    return os.path.abspath(path).replace(os.sep, "/")


class FFmpegService:
    # ------------------------------------------------------------------ probing
    def get_duration(self, media_path: str) -> float:
        """Duration in seconds of any audio or video file, via ffprobe."""
        if shutil.which("ffprobe") is None:
            raise RuntimeError(
                "ffprobe was not found on PATH. It ships in the same download/install "
                "as ffmpeg (same bin\\ folder on Windows) - if you just installed "
                "ffmpeg, make sure that folder actually contains ffprobe.exe too, "
                "and that you opened a new terminal after adding it to PATH."
            )
        result = _run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path,
        ])
        return float(result.stdout.strip())

    # Kept under the old name too (older callers / scripts).
    get_audio_duration = get_duration

    # ------------------------------------------------------------------ audio
    def build_audio_track(
        self,
        segment_paths: Sequence[str],
        gaps_after: Sequence[float],
        output_wav: str,
        loudness_normalize: bool = True,
        lead_in: float = 0.0,
    ) -> str:
        """
        Joins the spoken lines of a scene (narration + each character's lines)
        into one audio track, with a short natural pause after each line
        (`gaps_after[i]` seconds after segment i - the last value is the tail
        after the final line). `lead_in` adds silence before the first line.
        Output is 48 kHz stereo WAV so it only gets lossy-encoded once, at the
        very end.
        """
        _require_ffmpeg()
        if len(segment_paths) != len(gaps_after):
            raise ValueError("segment_paths and gaps_after must have the same length")
        if not segment_paths:
            raise ValueError("No audio segments to join.")

        cmd: List[str] = ["ffmpeg", "-y"]
        labels: List[str] = []
        filters: List[str] = []
        n_inputs = 0

        if lead_in and lead_in > 0.001:
            cmd += ["-f", "lavfi", "-t", f"{lead_in:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            filters.append(f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a0]")
            labels.append("[a0]")
            n_inputs = 1

        for path, gap in zip(segment_paths, gaps_after):
            cmd += ["-i", path]
            filters.append(
                f"[{n_inputs}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{n_inputs}]"
            )
            labels.append(f"[a{n_inputs}]")
            n_inputs += 1
            if gap and gap > 0.001:
                cmd += ["-f", "lavfi", "-t", f"{gap:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
                filters.append(f"[{n_inputs}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{n_inputs}]")
                labels.append(f"[a{n_inputs}]")
                n_inputs += 1

        chain = f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[cat]"
        if loudness_normalize:
            # loudnorm internally resamples to 192 kHz - bring it back to 48 kHz.
            chain += ";[cat]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[out]"
        else:
            chain += ";[cat]anull[out]"
        filters.append(chain)

        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", "[out]",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            output_wav,
        ]
        _run(cmd)
        return output_wav

    # ------------------------------------------------------------------ video
    def fit_clip(
        self,
        src_path: str,
        dst_path: str,
        duration: float,
        width: int,
        height: int,
        fps: int = 24,
        sharpen: float = 0.0,
        interpolate: bool = False,
    ) -> str:
        """
        Normalises one generated clip so every shot has identical codec / size /
        frame rate and EXACTLY the requested duration:

          * scaled to *cover* width x height and centre-cropped - never
            stretched, so faces keep their proportions
          * longer than needed -> trimmed
          * a little too short -> gently slowed (up to 30%) rather than frozen
          * still short -> the last frame is held for the remainder
          * optional mild sharpening to counter the video model's softness
        """
        _require_ffmpeg()
        frames = max(1, int(round(duration * fps)))
        target = frames / float(fps)
        src_len = max(self.get_duration(src_path), 0.04)

        stretch = 1.0
        if src_len < target:
            stretch = min(1.3, target / src_len)
        hold = max(0.0, target - src_len * stretch)

        vf = []
        if interpolate:
            # Low-frame-rate models (CogVideoX makes 8 fps) look choppy if frames are merely
            # repeated; this synthesises the in-between frames (motion-compensated).
            vf.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
        vf += [
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={width}:{height}",
            "setsar=1",
        ]
        if stretch > 1.001:
            vf.append(f"setpts={stretch:.4f}*PTS")
        vf.append(f"fps={fps}")
        if hold > 0.001:
            vf.append(f"tpad=stop_mode=clone:stop_duration={hold:.3f}")
        if sharpen and sharpen > 0:
            vf.append(f"unsharp=5:5:{sharpen:.2f}:5:5:0.0")
        vf.append("format=yuv420p")

        _run([
            "ffmpeg", "-y", "-i", src_path,
            "-vf", ",".join(vf),
            "-frames:v", str(frames),
            "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-r", str(fps),
            "-movflags", "+faststart",
            dst_path,
        ])
        return dst_path

    # ------------------------------------------------------------------ hybrid audio
    def has_audio(self, media_path: str) -> bool:
        """True if the file has a usable audio stream."""
        if shutil.which("ffprobe") is None:
            return False
        try:
            out = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", media_path]).stdout
            return "audio" in out
        except Exception:  # noqa: BLE001
            return False

    def extract_ambience(self, video_path: str, out_wav: str, seconds: float) -> str:
        """Takes the video model's own background sound from a clip: exactly `seconds` long
        (padded with silence if the clip's audio is shorter), 48 kHz stereo."""
        _require_ffmpeg()
        if self.has_audio(video_path):
            _run([
                "ffmpeg", "-y", "-i", video_path, "-vn",
                "-af", f"aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{seconds:.3f}",
                "-c:a", "pcm_s16le", out_wav,
            ])
        else:
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.3f}",
                  "-c:a", "pcm_s16le", out_wav])
        return out_wav

    def join_ambience(self, wavs: Sequence[str], crossfade: float, out_wav: str) -> str:
        """Joins per-shot ambience tracks with a short crossfade, so the background does not
        jump at every cut. Each input except the last is expected to be (shot length + crossfade)
        long, which makes the result exactly as long as the sum of the shot lengths."""
        _require_ffmpeg()
        if len(wavs) == 1:
            shutil.copyfile(wavs[0], out_wav)
            return out_wav
        cmd: List[str] = ["ffmpeg", "-y"]
        for w in wavs:
            cmd += ["-i", w]
        parts, prev = [], "[0:a]"
        for i in range(1, len(wavs)):
            label = f"[x{i}]" if i < len(wavs) - 1 else "[out]"
            parts.append(f"{prev}[{i}:a]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{label}")
            prev = label
        cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-c:a", "pcm_s16le", out_wav]
        _run(cmd)
        return out_wav

    def mix_voices_with_ambience(self, voices_wav: str, ambience_wav: str, out_wav: str, ambience_volume: float = 0.55) -> str:
        """Voices stay at full level; the background sound is mixed underneath and is automatically
        lowered ('ducked') whenever someone is speaking, then raised again in the pauses."""
        _require_ffmpeg()
        fc = (
            f"[1:a]volume={ambience_volume:.3f}[amb];"
            "[0:a]asplit=2[v_main][v_side];"
            "[amb][v_side]sidechaincompress=threshold=0.03:ratio=3:attack=20:release=450:makeup=1[ducked];"
            # amix scales every input by 1/n, so volume=2 restores the voices' level.
            "[v_main][ducked]amix=inputs=2:duration=first:dropout_transition=0,volume=2,alimiter=limit=0.95[out]"
        )
        _run(["ffmpeg", "-y", "-i", voices_wav, "-i", ambience_wav, "-filter_complex", fc,
              "-map", "[out]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", out_wav])
        return out_wav

    def extract_frame(self, video_path: str, output_png: str, at_seconds: float = 0.4) -> str:
        """Saves one still frame from a video (used as the scene thumbnail in text-to-video mode)."""
        _require_ffmpeg()
        _run(["ffmpeg", "-y", "-ss", f"{max(0.0, at_seconds):.2f}", "-i", video_path, "-frames:v", "1", "-q:v", "2", output_png])
        return output_png

    def concatenate_videos(
        self,
        video_paths: List[str],
        output_path: str,
        audio_codec: Optional[str] = None,
    ) -> str:
        """
        Concatenates clips that share identical encoding parameters (all clips
        here come out of fit_clip / assemble_scene, so they do).
        With audio_codec (e.g. "aac") the audio is re-encoded in one go, which
        avoids tiny glitches at AAC joins; the video is always stream-copied.
        """
        _require_ffmpeg()
        list_file_path = f"{output_path}.concat.txt"

        with open(list_file_path, "w") as f:
            for path in video_paths:
                # Single quotes inside a path must be escaped for the concat parser.
                safe = _fwd(path).replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path]
        if audio_codec:
            cmd += ["-c:v", "copy", "-c:a", audio_codec, "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        else:
            cmd += ["-c", "copy"]
        cmd += ["-movflags", "+faststart", output_path]
        try:
            _run(cmd)
        finally:
            if os.path.exists(list_file_path):
                os.remove(list_file_path)
        return output_path

    def assemble_scene(self, clip_paths: List[str], audio_wav: str, output_path: str) -> str:
        """
        Joins a scene's shots and lays the full voice track over them. The audio
        is padded with silence or trimmed to the video's length, but is NEVER
        cut short because a clip happened to be shorter than the speech (the
        old `-shortest` behaviour that chopped off dialogue).
        """
        _require_ffmpeg()
        silent = f"{output_path}.silent.mp4"
        self.concatenate_videos(clip_paths, silent)
        try:
            vdur = self.get_duration(silent)
            _run([
                "ffmpeg", "-y", "-i", silent, "-i", audio_wav,
                "-filter_complex", f"[1:a]apad,atrim=0:{vdur:.3f}[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart",
                output_path,
            ])
        finally:
            if os.path.exists(silent):
                os.remove(silent)
        return output_path

    def combine_scene_assets(self, video_path: str, audio_path: str, output_path: str) -> str:
        """
        Legacy helper: merges one video with one audio file. Fixed to never
        truncate the audio - if the audio is longer, the last video frame is
        held until the audio finishes.
        """
        _require_ffmpeg()
        vdur = self.get_duration(video_path)
        adur = self.get_duration(audio_path)
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path]
        if adur > vdur + 0.05:
            cmd += [
                "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={adur - vdur:.3f}[v]",
                "-map", "[v]", "-map", "1:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            ]
        else:
            cmd += ["-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest"]
        cmd += ["-c:a", "aac", "-b:a", "192k", output_path]
        _run(cmd)
        return output_path
