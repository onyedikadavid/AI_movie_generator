"""
Self-test for the NEW defaults: the video model speaks the lines itself (no edge-tts), the camera is free,
and stage directions are performed instead of being read aloud. Needs only ffmpeg + Pillow.

    python scripts/selftest_model_voice.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, HERE)

from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.ffmpeg_service import FFmpegService  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast, split_dialogue  # noqa: E402


class FakeModelVideo:
    """Returns clips WITH a voice-like tone, of whatever length was requested (like LTX-2.3 does)."""

    def __init__(self):
        self.t2v, self.i2v = [], []

    def _clip(self, path, duration, w, h):
        secs = (8 * int(min(duration * 24, 121) // 8) + 1) / 24
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=24:d={secs}",
                        "-f", "lavfi", "-i", f"sine=f=220:d={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", path], check=True, capture_output=True)

    def generate_text_clip(self, prompt, path, duration, seed=None, want_audio=None):
        self.t2v.append((prompt, duration, want_audio))
        self._clip(path, duration, 960, 544)
        return path

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False):
        self.i2v.append((os.path.basename(image), prompt, duration, want_audio))
        self._clip(path, duration, 768, 512)
        return path


def scene():
    return NS(
        scene_number=1, duration_seconds=6, location="Neon-lit sound lab",
        visual_description="A sound engineer works at a mixing board in a dark neon lab",
        image_prompt="Kobi at a mixing board in a neon lab", motion_prompt="slow dolly-in toward Kobi",
        sound_design="distant machinery hum, soft electronic beeps", characters_present=["Kobi"],
        narration_text="Kobi focuses on the sound wave.",
        dialogue_turns=[
            # EXACT lines from the editor screenshot: action and words are mixed in one string
            {"speaker": "Kobi", "text": 'Irritated Tapping on the mixing board... "What\'s with this distortion? Can\'t get the harmonics to resonate...',
             "expression": "neutral", "action": "stands attentively"},
            {"speaker": "Kobi", "text": "Frustrated Fiddling with the gain... Harmonics are all wrong, this is ridiculous... Why won't it just cooperate?",
             "expression": "neutral", "action": "stands attentively"},
            {"speaker": "Kobi", "text": "I dont know why this would not work", "expression": "neutral", "action": ""},
        ],
    )


def main():
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found"); sys.exit(1)
    ff = FFmpegService()
    tmp = tempfile.mkdtemp()
    chars = [NS(name="Kobi", description="a sound engineer, he is 30 years old",
                appearance_prompt="30-year-old Black man, short hair, acoustic goggles, dark hoodie",
                gender="male", age_group="adult", voice_id=None)]
    cast = VoiceCast(chars, "ng", "", texts=["Nollywood sound engineer"])
    settings.NARRATOR_ENABLED = False
    settings.AUDIO_MODE, settings.VIDEO_CAMERA_MOTION = "model", "model"

    # --- the splitter on the real screenshot lines ----------------------------------------------------
    a, w = split_dialogue(scene().dialogue_turns[0]["text"])
    assert a == "Irritated Tapping on the mixing board" and w.startswith("What's with this distortion?"), (a, w)
    a, w = split_dialogue(scene().dialogue_turns[1]["text"])
    assert a == "Frustrated Fiddling with the gain" and w.startswith("Harmonics are all wrong"), (a, w)
    assert split_dialogue("I dont know why this would not work") == ("", "I dont know why this would not work")
    print("OK action/words split on the screenshot lines")

    for mode in ("t2v", "i2v"):
        tts, img, vid = FakeTTS(), FakeImage(), FakeModelVideo()
        r = SceneRenderer(img, vid, tts, ff)
        res = r.render(scene(), chars, "Cinematic sci-fi", cast, os.path.join(tmp, "s_" + mode), "p1", FakeCtx(), video_mode=mode)
        assert tts.lines == [], "model-voice mode must not call text-to-speech at all"
        reqs = vid.t2v if mode == "t2v" else vid.i2v
        prompts = [x[0] if mode == "t2v" else x[1] for x in reqs]
        joined = " || ".join(prompts)
        # only the words are in quotes; the action is described, not quoted
        assert "What's with this distortion?" in joined and 'says in an adult male voice with a Nigerian accent' in joined
        assert 'Irritated Tapping on the mixing board"' not in joined and '"Irritated' not in joined, "action must never be quoted/spoken"
        assert "Kobi is irritated, tapping on the mixing board" in joined, joined[:400]
        assert "static" not in joined and "locked-off" not in joined and "only small gentle motion" not in joined
        assert "Camera:" in joined, "a free camera direction should be in the prompts"
        assert all((x[2] if mode == "t2v" else x[3]) for x in reqs), "the model's own sound must be requested"
        assert ff.has_audio(res.final_path) and res.shots >= 3
        if mode == "t2v":
            assert len(img.prompts) == 0, "t2v must not draw keyframes"
        else:
            assert len(img.prompts) >= 2, "i2v draws a wide + a close-up keyframe"
            assert any("medium shot (waist up)" in p for p in img.prompts), "free camera frames the speaker waist-up"
        print("OK %s model voices: %d clips (%.1fs), no TTS, camera free, action performed, sound requested" % (mode, res.shots, res.duration))
        print("   sample:", prompts[0][:330])

    # --- legacy edge-tts path now speaks ONLY the words ------------------------------------------------
    settings.AUDIO_MODE = "voices"
    tts = FakeTTS()
    r = SceneRenderer(FakeImage(), FakeModelVideo(), tts, ff)
    r.render(scene(), chars, "Cinematic", cast, os.path.join(tmp, "legacy"), "p1", FakeCtx(), video_mode="t2v")
    spoken = " ".join(t for _, _, t in tts.lines)
    assert "Tapping" not in spoken and "Fiddling" not in spoken and "Irritated" not in spoken, spoken
    assert "distortion" in spoken
    print("OK edge-tts path: stage directions are no longer read aloud ->", [t for _, _, t in tts.lines][:2])

    # --- the per-project mode changes the fingerprint so a switch re-renders --------------------------
    settings.AUDIO_MODE = "model"
    vid = FakeModelVideo(); img = FakeImage()
    r = SceneRenderer(img, vid, FakeTTS(), ff)
    d = os.path.join(tmp, "switch")
    r.render(scene(), chars, "Cinematic", cast, d, "p1", FakeCtx(), video_mode="t2v")
    n_t2v = len(vid.t2v)
    r.render(scene(), chars, "Cinematic", cast, d, "p1", FakeCtx(), video_mode="t2v")
    assert len(vid.t2v) == n_t2v, "unchanged -> reused"
    r.render(scene(), chars, "Cinematic", cast, d, "p1", FakeCtx(), video_mode="i2v")
    assert len(vid.i2v) > 0 and len(img.prompts) > 0, "switching mode re-renders the scene"
    print("OK switching text-to-video <-> image-first re-renders; unchanged scenes are reused")

    # --- a long line is split so no clip must hold a whole paragraph ----------------------------------
    long = scene(); long.dialogue_turns = [{"speaker": "Kobi", "text": "I have been working on this signal for three days now. Every time I think I have it, the harmonics collapse, and I start again from nothing at all.", "expression": "tired", "action": "rubs his eyes"}]
    vid = FakeModelVideo()
    r = SceneRenderer(FakeImage(), vid, FakeTTS(), ff)
    r.render(long, chars, "Cinematic", cast, os.path.join(tmp, "long"), "p1", FakeCtx(), video_mode="t2v")
    quoted = [p.split('"')[1] for p, _, _ in vid.t2v if '"' in p]
    assert len(quoted) >= 3 and all(len(q.split()) <= settings.MODEL_VOICE_MAX_WORDS + 1 for q in quoted), quoted
    print("OK long line split into %d clips:" % len(quoted), quoted)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL MODEL-VOICE CHECKS PASSED")


if __name__ == "__main__":
    main()
