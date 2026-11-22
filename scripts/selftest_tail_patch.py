"""
Self-test: model voice, long scene - a cut-off LAST line is not left half-spoken and the long clip is NOT made again.
The clip is cut back to the pause before the unfinished line and only that line is made in a short clip that carries on.
Needs ffmpeg + Pillow:   python scripts/selftest_tail_patch.py
"""
import os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
from app.core.config import settings  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402
from selftest_complete_lines import CHARS, scene, ff  # noqa: E402
from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402


class PauseVoiceVideo:
    """Fake model: each quoted line is a burst of `wps` words/second, 1.5 s pauses between lines, a quiet room tone all the
    time. A line that does not fit before the clip ends is simply still going at the end (cut off)."""

    def __init__(self, wps=1.0, limit_s=60.0, later_wps=None):
        self.wps, self.limit_s, self.calls, self.later_wps = wps, limit_s, [], later_wps

    def server_max_seconds(self):
        return self.limit_s

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False, resolution=None, audio_path=None):
        self.calls.append((re.findall(r'"([^"]+)"', prompt), round(duration, 2)))
        secs = (8 * int(min(duration, self.limit_s) * 24 // 8) + 1) / 24
        t, inputs, labels = 1.0, [], []
        for i, line in enumerate(self.calls[-1][0]):
            d = len(line.split()) / ((self.later_wps or self.wps) if len(self.calls) > 1 else self.wps)
            d_cut = max(0.0, min(d, secs - t))
            if d_cut > 0.05:
                inputs += ["-f", "lavfi", "-t", f"{d_cut:.2f}", "-i", "anoisesrc=c=pink:r=48000:a=0.30"]
                labels.append((len(labels) + 1, t))
            t += d + 1.5
        fc = f"anoisesrc=d={secs}:c=pink:r=48000:a=0.002[room];"
        for k, (idx, start) in enumerate(labels):
            ms = int(start * 1000)
            fc += f"[{idx}:a]adelay={ms}|{ms},apad=whole_dur={secs}[v{k}];"
        fc += "[room]" + "".join(f"[v{k}]" for k in range(len(labels))) + f"amix=inputs={1 + len(labels)}:duration=longest:normalize=0[a]"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=960x544:r=24:d={secs}"] + inputs +
                       ["-filter_complex", fc.replace("[room]", "[room]", 1), "-map", "0:v", "-map", "[a]", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", f"{secs}", path], check=True, capture_output=True)
        return path


def main():
    tmp = tempfile.mkdtemp(prefix="tail_")
    try:
        settings.STORAGE_DIR = tmp
        settings.AUDIO_MODE = "model"
        settings.SPEECH_COMPLETE_CHECK = True
        settings.SPEECH_TAIL_PATCH = True
        settings.SPEECH_FALLBACK_TTS = False
        settings.VIDEO_MAX_CLIP_SECONDS = 600.0
        settings.SPEECH_RETRY_MAX_RENDER_SECONDS = 0   # no regenerate-the-long-clip path in this test
        settings.SPEECH_MAX_RETRIES = 0
        lines = [("Ada", "Good morning class, today we learn where an apple comes from."),
                 ("Kobi", "Is it true that apples grow on very tall trees in the north?"),
                 ("Ada", "Yes, and the farmers pick them carefully by hand every autumn morning.")]
        sc = scene(lines)
        vid = PauseVoiceVideo(wps=0.3, limit_s=600.0, later_wps=3.0)   # slow voice in the 2-minute clip: the third line is cut off
        r = SceneRenderer(FakeImage(), vid, FakeTTS(), ff)
        sdir = os.path.join(tmp, "projects", "p1", "scene_1")
        res = r.render(sc, CHARS, "Cinematic", VoiceCast(CHARS), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=120)
        assert len(vid.calls) == 2, vid.calls
        first, second = vid.calls
        assert len(first[0]) == 3, first
        assert len(second[0]) == 1 and "farmers" in second[0][0], second
        assert not ff.tail_speech_active(os.path.join(sdir, "whole_1_raw.mp4"))[0], "the finishing clip must end after its line"
        full_first = ff.get_duration(os.path.join(sdir, "whole_0_raw.mp4"))
        assert res.duration < full_first + second[1] and res.duration > 20, (res.duration, full_first)
        print(f"OK 1: cut-off last line -> clip cut back, only that line made again ({second[1]:.0f}s clip); scene {res.duration:.0f}s")

        # a second run reuses both clips and still joins the finishing clip (the cut point is remembered)
        n_calls = len(vid.calls)
        res2 = r.render(sc, CHARS, "Cinematic", VoiceCast(CHARS), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=120)
        assert len(vid.calls) == n_calls and abs(res2.duration - res.duration) < 0.5, (len(vid.calls), res2.duration, res.duration)
        print("OK 2: nothing is made twice; the finishing clip is still joined")

        # all lines fit -> no extra clip
        vid3 = PauseVoiceVideo(wps=3.0, limit_s=600.0)
        r3 = SceneRenderer(FakeImage(), vid3, FakeTTS(), ff)
        r3.render(sc, CHARS, "Cinematic", VoiceCast(CHARS), os.path.join(tmp, "projects", "p2", "scene_1"), "p2", FakeCtx(), video_mode="i2v", scene_seconds=120)
        assert len(vid3.calls) == 1, vid3.calls
        print("OK 3: lines that fit -> one clip, nothing added")
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
