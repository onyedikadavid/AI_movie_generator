"""
Self-test for the scene renderer. Needs ONLY ffmpeg + Pillow (no GPU, no
network, no database): the AI servers are replaced by fakes that produce real
image/audio/video files, so this exercises the real timeline, shot cutting,
fitting, audio mixing, resume and stop logic.

    python scripts/selftest_renderer.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image  # noqa: E402

from app.services.ffmpeg_service import FFmpegService  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402


class Stopped(Exception):
    pass


class FakeCtx:
    def __init__(self):
        self.stages, self.stop_after_calls, self.calls = [], None, 0

    def checkpoint(self):
        if self.stop_after_calls is not None and self.calls >= self.stop_after_calls:
            raise Stopped()

    def call(self, fn, *a, **kw):
        self.calls += 1
        return fn(*a, **kw)

    def stage(self, status, frac, detail):
        self.stages.append((status, round(frac, 2), detail))

    def set_scene_image(self, path):
        self.image = path


class FakeTTS:
    def __init__(self):
        self.lines = []

    def synthesize(self, text, profile, path, speaker=""):
        self.lines.append((profile.label, profile.voice, text))
        secs = max(0.8, len(text.split()) / 2.6)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=f={200 + 40 * len(self.lines)}:d={secs}",
                        "-c:a", "libmp3lame", path], check=True, capture_output=True)
        return path


class FakeImage:
    def __init__(self):
        self.prompts = []

    def generate_image(self, prompt, path, negative="", width=None, height=None, seed=None):
        self.prompts.append(prompt)
        Image.new("RGB", (1216, 832), (30 + 20 * len(self.prompts) % 200, 90, 140)).save(path)
        return path


class FakeVideo:
    def __init__(self):
        self.requests = []

    def generate_clip(self, image, path, prompt, duration, seed=None):
        self.requests.append((os.path.basename(image), prompt, duration))
        frames = 8 * int(min(duration * 24, 121) // 8) + 1
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s=768x512:r=24:d={frames / 24}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True, capture_output=True)
        return path


def make_scene():
    return NS(
        scene_number=1, duration_seconds=5, location="Village compound",
        visual_description="A boy kneels before his father in a dusty compound",
        image_prompt="A Nigerian boy kneeling before an elderly man in a dusty compound",
        motion_prompt="slow push-in, dust drifting", characters_present=["Obinna", "Daddy"],
        narration_text="Years later, Obinna returned to the village to face his guardian.",
        dialogue_turns=[
            {"speaker": "Obinna", "text": "Daddy, please forgive me.", "expression": "pleading", "action": "kneeling"},
            {"speaker": "Daddy", "text": "No. I will not forgive you after what you did to this family.", "expression": "stern", "action": "turns away"},
            {"speaker": "Obinna", "text": "I was a child. I have changed, please.", "expression": "sad", "action": "weeping"},
        ],
    )


def main():
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found"); sys.exit(1)
    tmp = tempfile.mkdtemp()
    chars = [
        NS(name="Obinna", description="a boy, he is 15 years old", appearance_prompt="teenage Nigerian boy, short black hair, white shirt", gender=None, age_group=None, voice_id=None),
        NS(name="Daddy", description="elderly guardian", appearance_prompt="old man in a grey agbada", gender="male", age_group=None, voice_id=None),
    ]
    cast = VoiceCast(chars, "auto", texts=["Nigerian boy Obinna"])
    scene = make_scene()
    ff = FFmpegService()
    tts, img, vid = FakeTTS(), FakeImage(), FakeVideo()
    r = SceneRenderer(img, vid, tts, ff)
    sd = os.path.join(tmp, "scene_1")

    # --- full render ---------------------------------------------------------
    ctx = FakeCtx()
    res = r.render(scene, chars, "Nollywood cinematic", cast, sd, "proj1", ctx)
    print("scene length %.2fs, %d shots" % (res.duration, res.shots))
    voices = {label: voice for label, voice, _ in tts.lines}
    print("voices:", voices)
    assert voices["Narrator"] != voices["Obinna"] != voices["Daddy"] and len(set(voices.values())) == 3
    assert len(tts.lines) == 4, "narration + 3 dialogue lines should all be spoken"
    assert abs(ff.get_duration(res.final_path) - res.duration) < 0.4, "video length must match speech timeline"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "csv=p=0", res.final_path],
                           capture_output=True, text=True).stdout
    assert "video,1280,720" in probe and "audio" in probe, probe
    # close-ups were used for speaking shots
    assert any("closeup_obinna" in r_[0] for r_ in vid.requests) and any("closeup_daddy" in r_[0] for r_ in vid.requests), vid.requests
    assert any(r_[0] == "keyframe.png" for r_ in vid.requests), "narration shot should use the wide keyframe"
    assert all(r_[2] <= 5.0 for r_ in vid.requests)
    assert len(img.prompts) == 3
    print("OK full render:", len(vid.requests), "video clips,", len(img.prompts), "images")

    # --- resume: nothing changed -> nothing regenerated -----------------------
    calls_before = (len(tts.lines), len(img.prompts), len(vid.requests))
    res2 = r.render(scene, chars, "Nollywood cinematic", cast, sd, "proj1", FakeCtx())
    assert (len(tts.lines), len(img.prompts), len(vid.requests)) == calls_before
    print("OK resume of a finished scene reuses everything")

    # --- stop mid-scene, then resume only does the remaining work -------------
    shutil.rmtree(sd)
    tts2, img2, vid2 = FakeTTS(), FakeImage(), FakeVideo()
    r2 = SceneRenderer(img2, vid2, tts2, ff)
    ctx = FakeCtx(); ctx.stop_after_calls = 7  # 4 voices + 3 images -> stops before the first clip
    try:
        r2.render(scene, chars, "Nollywood cinematic", cast, sd, "proj1", ctx)
        raise SystemExit("expected a stop")
    except Stopped:
        pass
    done_voices, done_imgs = len(tts2.lines), len(img2.prompts)
    r2.render(scene, chars, "Nollywood cinematic", cast, sd, "proj1", FakeCtx())
    assert len(tts2.lines) == done_voices and len(img2.prompts) == done_imgs, "resume must not redo finished work"
    assert len(vid2.requests) >= 3
    print("OK stop + resume: kept %d voices, %d images; rendered the %d clips that were missing" % (done_voices, done_imgs, len(vid2.requests)))

    # --- editing the scene invalidates the cache ------------------------------
    scene.narration_text = "A completely different narration line."
    n = len(tts2.lines)
    r2.render(scene, chars, "Nollywood cinematic", cast, sd, "proj1", FakeCtx())
    assert len(tts2.lines) > n
    print("OK editing a scene re-renders it")

    # --- ambient (no speech at all) -------------------------------------------
    quiet = make_scene(); quiet.dialogue_turns = None; quiet.narration_text = None
    res3 = r.render(quiet, chars, "Nollywood", cast, os.path.join(tmp, "scene_2"), "proj1", FakeCtx())
    assert res3.shots >= 1 and res3.duration > 3
    print("OK scene with no speech still renders (%.1fs)" % res3.duration)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL RENDERER CHECKS PASSED")


if __name__ == "__main__":
    main()
