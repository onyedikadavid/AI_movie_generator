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


class FakeVideoT2V(FakeVideo):
    def __init__(self, with_audio=False):
        super().__init__()
        self.text_requests = []
        self.with_audio = with_audio

    def generate_text_clip(self, prompt, path, duration, seed=None):
        self.text_requests.append((prompt, duration, seed))
        frames = 8 * int(min(duration * 24, 121) // 8) + 1
        secs = frames / 24
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s=960x544:r=24:d={secs}"]
        if self.with_audio:   # steady "wind" background, like the model's own sound
            cmd += ["-f", "lavfi", "-i", f"anoisesrc=d={secs}:c=pink:a=0.2:r=48000", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
        subprocess.run(cmd, check=True, capture_output=True)
        return path


def make_scene():
    return NS(
        scene_number=1, duration_seconds=5, location="Village compound",
        visual_description="A boy kneels before his father in a dusty compound",
        image_prompt="A Nigerian boy kneeling before an elderly man in a dusty compound",
        motion_prompt="slow push-in, dust drifting", sound_design="light wind, distant roosters, dry leaves", characters_present=["Obinna", "Daddy"],
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

    # --- text-to-video mode: no keyframes, prompts carry the looks, thumbnail from first clip ---
    from app.core.config import settings
    settings.VIDEO_MODE = "t2v"
    try:
        tts3, img3, vid3 = FakeTTS(), FakeImage(), FakeVideoT2V()
        r3 = SceneRenderer(img3, vid3, tts3, ff)
        shown = []
        ctx3 = FakeCtx()
        ctx3.set_scene_image = lambda p: shown.append(p)
        res4 = r3.render(make_scene(), chars, "Nollywood cinematic", cast, os.path.join(tmp, "scene_t2v"), "proj1", ctx3)
        assert len(img3.prompts) == 0, "text-to-video must not draw any keyframes"
        assert len(vid3.requests) == 0 and len(vid3.text_requests) == res4.shots >= 2
        assert shown and os.path.getsize(shown[0]) > 1000, "a thumbnail should be cut from the first clip"
        joined = " || ".join(p for p, _, _ in vid3.text_requests)
        assert "teenage Nigerian boy" in joined and "grey agbada" in joined, "character looks must be repeated in the prompts"
        assert all("static locked-off camera" in p for p, _, _ in vid3.text_requests)
        seeds = {}
        for p, _, sd in vid3.text_requests:
            seeds.setdefault(("Obinna" in p.split(".")[0]), set()).add(sd)
        assert abs(ff.get_duration(res4.final_path) - res4.duration) < 0.4
        print("OK text-to-video: %d clips, 0 keyframes, thumbnail extracted, looks repeated in every prompt" % res4.shots)
        print("   sample prompt:", vid3.text_requests[1][0][:260], "...")
    finally:
        settings.VIDEO_MODE = "i2v"

    # --- hybrid sound: model background under the voices; none in voices-only mode; graceful with silent clips ---
    import numpy as np

    def audio_level(path, a, b):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", path, "-vn",
                              "-f", "f32le", "-ac", "1", "-ar", "48000", "-"], capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0

    results = {}
    for label, mode, with_audio in (("hybrid", "hybrid", True), ("voices-only", "voices", True), ("hybrid-silent-clips", "hybrid", False)):
        settings.VIDEO_MODE, settings.AUDIO_MODE = "t2v", mode
        try:
            v = FakeVideoT2V(with_audio=with_audio)
            r4 = SceneRenderer(FakeImage(), v, FakeTTS(), ff)
            res5 = r4.render(make_scene(), chars, "Nollywood cinematic", cast, os.path.join(tmp, "scene_" + label), "proj1", FakeCtx())
            assert abs(ff.get_duration(res5.final_path) - res5.duration) < 0.4
            assert ff.has_audio(res5.final_path)
            results[label] = (audio_level(res5.final_path, 0.02, 0.28), v.text_requests)   # lead-in: nobody is speaking yet
        finally:
            settings.VIDEO_MODE, settings.AUDIO_MODE = "i2v", "hybrid"
    hyb, vo, sil = results["hybrid"][0], results["voices-only"][0], results["hybrid-silent-clips"][0]
    assert hyb > 0.01, f"hybrid should have background sound in the pauses (got {hyb})"
    assert vo < 0.002 and sil < 0.002, (vo, sil)
    assert all("no spoken dialogue" in p for p, _, _ in results["hybrid"][1]), "hybrid prompts must tell the model not to add speech"
    assert not any("no spoken dialogue" in p for p, _, _ in results["voices-only"][1])
    print("OK hybrid sound: background level in a pause = %.3f (hybrid) vs %.4f (voices-only) vs %.4f (silent clips)" % (hyb, vo, sil))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL RENDERER CHECKS PASSED")


if __name__ == "__main__":
    main()
