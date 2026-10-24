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
        self.cap = 121

    def _clip(self, path, duration, w, h):
        secs = (8 * int(min(duration * 24, self.cap) // 8) + 1) / 24
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=24:d={secs}",
                        "-f", "lavfi", "-i", f"sine=f=220:d={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", path], check=True, capture_output=True)

    def generate_text_clip(self, prompt, path, duration, seed=None, want_audio=None, resolution=None):
        self.seen_res = getattr(self, "seen_res", []) + [resolution]
        self.t2v.append((prompt, duration, want_audio))
        self._clip(path, duration, 960, 544)
        return path

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False, resolution=None):
        self.seen_res = getattr(self, "seen_res", []) + [resolution]
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
        res = r.render(scene(), chars, "Cinematic sci-fi", cast, os.path.join(tmp, "s_" + mode), "p1", FakeCtx(), video_mode=mode,
                       resolution="640x352")
        assert vid.seen_res and all(x == "640x352" for x in vid.seen_res), ("project resolution must reach the video client", vid.seen_res)
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
    # --- whole-scene mode: ONE clip for the scene, every line in one ordered prompt ---------------------
    settings.SCENE_MODE, settings.WHOLE_SCENE_MAX_SECONDS = "whole", 30.0
    for mode in ("t2v", "i2v"):
        vid = FakeModelVideo(); vid.cap = 721
        img = FakeImage()
        r = SceneRenderer(img, vid, FakeTTS(), ff)
        d = os.path.join(tmp, "whole_" + mode)
        res = r.render(scene(), chars, "Cinematic", cast, d, "p1", FakeCtx(), video_mode=mode)
        reqs = vid.t2v if mode == "t2v" else vid.i2v
        assert len(reqs) == 1 and res.shots == 1, "the whole scene must be ONE generation, got %d" % len(reqs)
        pr = reqs[0][0] if mode == "t2v" else reqs[0][1]
        assert pr.index("distortion") < pr.index("Harmonics are all wrong") < pr.index("I dont know"), pr
        assert "Kobi is irritated, tapping on the mixing board" in pr and '"Irritated' not in pr
        assert pr.count("Camera:") == 1 and "static" not in pr
        if mode == "i2v":
            assert len(img.prompts) == 1, "i2v whole-scene draws only the one wide keyframe"
        n = len(reqs)
        r.render(scene(), chars, "Cinematic", cast, d, "p1", FakeCtx(), video_mode=mode)
        assert len(reqs) == n, "finished scene is reused"
        print("OK whole scene (%s): 1 generation, %.1fs, lines in order" % (mode, res.duration))
        print("   prompt:", pr[:420])
    # DEFAULT: a scene longer than the cap is still ONE generation - the lines are shortened to fit
    big = scene(); big.dialogue_turns = [{"speaker": "Kobi", "text": "This is line number %d, and it has a handful of words in it." % i, "action": "pacing"} for i in range(8)]
    vid = FakeModelVideo(); vid.cap = 289
    settings.WHOLE_SCENE_MAX_SECONDS = 12.0
    res = SceneRenderer(FakeImage(), vid, FakeTTS(), ff).render(big, chars, "Cinematic", cast, os.path.join(tmp, "big"), "p1", FakeCtx(), video_mode="t2v")
    assert len(vid.t2v) == 1 and vid.t2v[0][1] <= 12.01, [(round(d_, 1)) for _, d_, _ in vid.t2v]
    print("OK long scene -> still ONE generation of %.1f s (lines shortened to fit)" % vid.t2v[0][1])
    # a server that only makes 2.7 s clips is respected (asked via /health) - still one generation
    class Limited(FakeModelVideo):
        def server_max_seconds(self):
            return 64 / 24.0
    vid = Limited(); vid.cap = 65
    res = SceneRenderer(FakeImage(), vid, FakeTTS(), ff).render(scene(), chars, "Cinematic", cast, os.path.join(tmp, "lim"), "p1", FakeCtx(), video_mode="t2v")
    assert len(vid.t2v) == 1 and vid.t2v[0][1] <= 2.7, vid.t2v[0][1]
    print("OK server limit 2.7 s -> one generation of %.1f s" % vid.t2v[0][1])
    # optional: allow splitting
    settings.WHOLE_SCENE_SPLIT = True
    vid = FakeModelVideo(); vid.cap = 289
    settings.WHOLE_SCENE_MAX_SECONDS = 12.0
    SceneRenderer(FakeImage(), vid, FakeTTS(), ff).render(big, chars, "Cinematic", cast, os.path.join(tmp, "split"), "p1", FakeCtx(), video_mode="t2v")
    assert 2 <= len(vid.t2v) <= 8 and all(d_ <= 12.01 for _, d_, _ in vid.t2v)
    print("OK WHOLE_SCENE_SPLIT=true -> %d pieces of <= 12 s" % len(vid.t2v))
    settings.WHOLE_SCENE_SPLIT = False
    # truncated clip: kept by default (never generate twice); fallback to shots only if asked
    settings.WHOLE_SCENE_MAX_SECONDS = 30.0
    vid = FakeModelVideo(); vid.cap = 121
    res = SceneRenderer(FakeImage(), vid, FakeTTS(), ff).render(scene(), chars, "Cinematic", cast, os.path.join(tmp, "keep"), "p1", FakeCtx(), video_mode="t2v")
    assert len(vid.t2v) == 1 and res.shots == 1
    settings.WHOLE_SCENE_FALLBACK = True
    vid = FakeModelVideo(); vid.cap = 121
    res = SceneRenderer(FakeImage(), vid, FakeTTS(), ff).render(scene(), chars, "Cinematic", cast, os.path.join(tmp, "fb"), "p1", FakeCtx(), video_mode="t2v")
    assert res.shots >= 3 and ff.has_audio(res.final_path), "fallback to shots"
    settings.WHOLE_SCENE_FALLBACK = False
    print("OK truncated clip kept by default; WHOLE_SCENE_FALLBACK=true redoes it as %d shots" % res.shots)
    # per-project narrator switch (overrides .env both ways)
    for nar in (True, False):
        settings.NARRATOR_ENABLED = not nar
        vid = FakeModelVideo(); vid.cap = 721
        SceneRenderer(FakeImage(), vid, FakeTTS(), ff).render(scene(), chars, "Cinematic", cast, os.path.join(tmp, "nar%s" % nar), "p1", FakeCtx(), video_mode="t2v", narrator=nar)
        has = "off-screen narrator" in vid.t2v[0][0].lower() or "narrator says" in vid.t2v[0][0].lower()
        assert has == nar, (nar, vid.t2v[0][0][:600])
    settings.NARRATOR_ENABLED = False
    print("OK narrator on/off comes from the project, not .env")
    # a scene that carries on from the previous one starts from its last frame (even in text-to-video mode)
    settings.WHOLE_SCENE_MAX_SECONDS = 30.0
    vid = FakeModelVideo(); vid.cap = 721; img = FakeImage()
    r = SceneRenderer(img, vid, FakeTTS(), ff)
    first = r.render(scene(), chars, "Cinematic", cast, os.path.join(tmp, "c1"), "p1", FakeCtx(), video_mode="t2v")
    nxt = scene(); nxt.scene_number = 2; nxt.continues_previous = True
    res = r.render(nxt, chars, "Cinematic", cast, os.path.join(tmp, "c2"), "p1", FakeCtx(), video_mode="t2v", prev_clip=first.final_path)
    assert len(vid.i2v) == 1 and vid.i2v[0][0] == "start_frame.png" and "Continuing the same shot" in vid.i2v[0][1], vid.i2v
    assert len(img.prompts) == 0, "carrying on needs no image model"
    nxt2 = scene(); nxt2.scene_number = 3; nxt2.continues_previous = False
    r.render(nxt2, chars, "Cinematic", cast, os.path.join(tmp, "c3"), "p1", FakeCtx(), video_mode="t2v", prev_clip=first.final_path)
    assert len(vid.i2v) == 1, "a normal cut still starts from text"
    print("OK 'continues previous' scene starts from the last frame of the previous scene; a normal cut does not")
    settings.SCENE_MODE = "shots"

    print("\nALL MODEL-VOICE CHECKS PASSED")


if __name__ == "__main__":
    main()
