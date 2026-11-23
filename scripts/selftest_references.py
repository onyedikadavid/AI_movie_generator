"""
Self-test (v2.35): a reference-image video model (Wan VACE notebook).
 - the bible pictures go WITH the request: the place first, then the people on screen (speakers first)
 - the clip is silent, so every line is spoken whole by the pipeline's voice at the time the prompt gave for it
 - the prompt shows the actions at their times and does NOT contain the quoted words or an Audio line
Needs ffmpeg + Pillow:   python scripts/selftest_references.py
"""
import os, re, shutil, subprocess, sys, tempfile
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
from PIL import Image  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import bible_assets  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402
from selftest_complete_lines import scene, ff  # noqa: E402
from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402

CH = [NS(id="c1", name="Ada", description="a teacher", appearance_prompt="a woman in a red dress", gender="female", age_group="adult", voice_id=None),
      NS(id="c2", name="Kobi", description="a boy", appearance_prompt="a boy in a green jacket", gender="male", age_group="child", voice_id=None)]


class SilentRefVideo:
    def __init__(self):
        self.calls = []

    wants = False

    def server_max_seconds(self):
        return 600.0

    def server_wants_references(self):
        return SilentRefVideo.wants

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False, resolution=None, audio_path=None,
                      reference_images=None, reference_has_place=False, window_beats=None):
        self.calls.append(dict(prompt=prompt, refs=[os.path.basename(r) for r in (reference_images or [])], place=reference_has_place,
                               audio=want_audio, duration=duration, beats=window_beats))
        secs = (8 * int(min(duration, 60) * 24 // 8) + 1) / 24
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=24:d={secs}", "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True, capture_output=True)
        return path


def main():
    tmp = tempfile.mkdtemp(prefix="refs_")
    try:
        settings.STORAGE_DIR = tmp
        settings.VIDEO_REFERENCES = False
        SilentRefVideo.wants = True           # the notebook says so on /health: no need to set VIDEO_REFERENCES in .env
        settings.WHOLE_ONLY = True
        settings.WHOLE_SCENE_STRICT = True
        settings.VIDEO_MAX_CLIP_SECONDS = 600.0
        settings.SPEECH_COMPLETE_CHECK = True
        settings.AUDIO_MODE = "model"
        bd = bible_assets.bible_dir("p1")
        os.makedirs(bd, exist_ok=True)
        for f in ("character_c1.png", "character_c2.png", "place_loc1.png"):
            Image.new("RGB", (320, 400), (90, 90, 140)).save(os.path.join(bd, f))
        lines = [("Kobi", "Is it true that apples grow on tall trees in the north?"),
                 ("Ada", "Yes, and the farmers pick them carefully by hand every autumn morning.")]
        sc = scene(lines)
        sc.place_id = "loc1"
        vid, tts = SilentRefVideo(), FakeTTS()
        r = SceneRenderer(FakeImage(), vid, tts, ff)
        sdir = os.path.join(tmp, "projects", "p1", "scene_1")
        res = r.render(sc, CH, "Cinematic", VoiceCast(CH), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=40)
        assert len(vid.calls) == 1, vid.calls
        c = vid.calls[0]
        assert c["refs"][0] == "place_loc1.png" and set(c["refs"][1:]) == {"character_c1.png", "character_c2.png"}, c["refs"]   # the place first, then the people
        assert c["place"] and c["audio"] is False, c
        assert "apples grow" not in c["prompt"] and "Audio:" not in c["prompt"], c["prompt"]
        assert c["beats"] and len(c["beats"]) == 2 and c["beats"][0][0] < c["beats"][1][0], c["beats"]   # the actions with their times
        assert "lips moving" in c["beats"][0][1] and "At 0:" not in c["prompt"], (c["beats"], c["prompt"])
        assert len(tts.lines) == 2, tts.lines
        raw = os.path.join(sdir, "whole_0_raw.mp4")
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", raw],
                               capture_output=True, text=True).stdout.strip()
        assert probe, "the clip must carry the voices"
        print(f"OK 1: refs sent = {c['refs']}; silent prompt; 2 lines spoken whole; scene {res.duration:.0f}s")

        n = len(vid.calls)
        r.render(sc, CH, "Cinematic", VoiceCast(CH), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=40)
        assert len(vid.calls) == n, "a finished clip is reused"
        print("OK 2: finished clip reused")

        Image.new("RGB", (320, 400), (200, 60, 60)).save(os.path.join(bd, "character_c2.png"))   # Kobi redrawn: a different file size is enough? make it bigger
        Image.new("RGB", (640, 800), (200, 60, 60)).save(os.path.join(bd, "character_c2.png"))
        r.render(sc, CH, "Cinematic", VoiceCast(CH), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=40)
        assert len(vid.calls) == n + 1, "a redrawn character makes the scene render again"
        print("OK 3: a changed bible picture re-renders the scene")
        sc2 = scene([("Kobi", "I like the red apples best of all, please tell me more about them.")])
        sc2.place_id = "loc1"
        sc2.characters_present = ["Ada", "Kobi"]
        vid2 = SilentRefVideo()
        SceneRenderer(FakeImage(), vid2, FakeTTS(), ff).render(sc2, CH, "Cinematic", VoiceCast(CH), os.path.join(tmp, "projects", "p1", "scene_2"), "p1", FakeCtx(), video_mode="i2v", scene_seconds=40)
        assert vid2.calls[0]["refs"][:2] == ["place_loc1.png", "character_c2.png"], vid2.calls[0]["refs"]   # the speaker comes before the listener
        print("OK 4: the speaker's picture comes before the listener's")
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
