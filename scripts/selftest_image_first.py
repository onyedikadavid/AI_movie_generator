"""
Self-test: image-first (v2.31).
  1. a scene's picture is drawn from the scene description, or from the user's OWN prompt (cast, place, style added around it);
  2. the picture lands in <project>/images/scene_<n>.png (outside the scene folders);
  3. a scene with an approved picture is rendered FROM IT: no new picture is drawn, and the video model starts from it;
  4. replacing the approved picture makes the scene render again (its clip is out of date).
Needs ffmpeg + Pillow:   python scripts/selftest_image_first.py
"""
import os, shutil, sys, tempfile
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
from PIL import Image  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import scene_images  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402
from selftest_complete_lines import SlowVoiceVideo, scene, ff, CHARS  # noqa: E402
from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402


class SeeingVideo(SlowVoiceVideo):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.images = []

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False, resolution=None, audio_path=None):
        self.images.append(image)
        return super().generate_clip(image, path, prompt, duration, seed, want_audio, resolution, audio_path)


def main():
    tmp = tempfile.mkdtemp(prefix="imgfirst_")
    try:
        settings.STORAGE_DIR = tmp
        settings.AUDIO_MODE = "model"; settings.VIDEO_MAX_CLIP_SECONDS = 600.0; settings.SPEECH_COMPLETE_CHECK = False
        sc = NS(scene_number=2, image_prompt="Two men at a workbench", visual_description="a workshop", place_desc="a timber workshop")
        img = FakeImage()
        path = scene_images.draw(img, sc, CHARS, "Cinematic", "p1")
        assert path == scene_images.approved_path("p1", 2) and os.path.getsize(path) > 0, path
        assert "workbench" in img.prompts[-1] and "timber workshop" in img.prompts[-1]
        scene_images.draw(img, sc, CHARS, "Cinematic", "p1", prompt="a lone carpenter at dawn, golden light")
        assert "lone carpenter" in img.prompts[-1] and "workbench" not in img.prompts[-1], img.prompts[-1]
        print("OK 1-2: picture drawn from the scene or from the user's own words, stored in <project>/images/")

        proj = os.path.join(tmp, "projects", "p1")
        s1 = scene([("Ada", "Good morning class, today we learn where an apple comes from.")])
        s1.scene_number = 1
        appr = scene_images.approved_path("p1", 1)
        os.makedirs(os.path.dirname(appr), exist_ok=True)
        Image.new("RGB", (1216, 832), (200, 30, 30)).save(appr)
        img2, vid = FakeImage(), SeeingVideo(wps=3.0, limit_s=700.0)
        r = SceneRenderer(img2, vid, FakeTTS(), ff)
        sdir = os.path.join(proj, "scene_1")
        r.render(s1, CHARS, "Cinematic", VoiceCast(CHARS), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=120)
        assert not img2.prompts, "an approved picture must not be drawn again"
        assert len(vid.calls) == 1 and vid.calls[0][1] >= 119, vid.calls
        assert vid.images and os.path.getsize(vid.images[0]) == os.path.getsize(appr), "the video must start from the approved picture"
        print("OK 3: rendered from the approved picture (nothing redrawn), one 2-minute clip")

        # same picture, same settings -> the finished clip is reused (no second request)
        r.render(s1, CHARS, "Cinematic", VoiceCast(CHARS), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=120)
        assert len(vid.calls) == 1, "unchanged picture + settings: reuse the finished clip"
        Image.new("RGB", (1216, 832), (30, 200, 30)).save(appr)      # the user redrew it
        os.utime(appr, (os.path.getmtime(appr) + 5, os.path.getmtime(appr) + 5))
        r.render(s1, CHARS, "Cinematic", VoiceCast(CHARS), sdir, "p1", FakeCtx(), video_mode="i2v", scene_seconds=120)
        assert len(vid.calls) == 2, f"a new picture must make a new clip {vid.calls}"
        print("OK 4: unchanged = reused, replaced picture = rendered again")
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
