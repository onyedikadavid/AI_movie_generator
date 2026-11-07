"""
Self-test for: conversations (per-shot camera, characters who stay put and face each other), lines that are cut off
(detected from the clip's own audio, then generated again longer), and audio-driven video models (MultiTalk /
InfiniteTalk / Hunyuan Avatar get each shot's slice of the voice track). Needs only ffmpeg + Pillow.

    python scripts/selftest_dialogue_and_speech.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from app.core.config import settings  # noqa: E402
from app.services import prompt_builder as pb  # noqa: E402
from app.services.ffmpeg_service import FFmpegService  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402
from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402

CHARS = [
    NS(name="Kobi", description="a sound engineer", appearance_prompt="a tall man in his thirties, short black hair, green jacket",
       gender="male", age_group="adult", voice_id=None),
    NS(name="Ada", description="a singer", appearance_prompt="a woman in her late twenties, braided hair, red dress",
       gender="female", age_group="adult", voice_id=None),
]


def scene(action="stands at the mixing board", motion="slow pan across the studio"):
    return NS(
        scene_number=1, duration_seconds=8, location="Recording studio", visual_description="A dim studio with a mixing board",
        image_prompt="Kobi and Ada in a studio", motion_prompt=motion, sound_design="soft hum", characters_present=["Kobi", "Ada"],
        narration_text="",
        dialogue_turns=[
            {"speaker": "Kobi", "text": "This track still sounds wrong to me, and I cannot tell why.", "expression": "irritated", "action": action},
            {"speaker": "Ada", "text": "Then play it again from the start and let me hear it properly.", "expression": "calm", "action": ""},
        ],
    )


def check_prompts():
    from app.core.config import settings as _st
    _st.CONVERSATION_STAGING = "stay"
    p = pb.text_video_prompt("dialogue", "Kobi", "irritated", "Walking up and down the studio", CHARS[0].appearance_prompt, CHARS,
                             "a recording studio", "a dim studio", "Cinematic", 1, "auto", None, False, "slow pan", "", "", model_voice=False)
    assert "Over-the-shoulder" in p and "facing Ada" in p and "no walking" in p and "Ada" in p, p
    assert "Walking up and down" not in p and "tracking" not in p.lower() and "arc" not in p.lower(), p
    a = pb.text_video_prompt("dialogue", "Kobi", "terrified", "falls from the sky screaming", CHARS[0].appearance_prompt, CHARS,
                             "above the clouds", "open sky", "Cinematic", 1, "auto", None, False, "camera dives after him", "", "", model_voice=False)
    assert "no walking" not in a and "dynamic" in a.lower() or "sweeping" in a.lower() or "handheld" in a.lower() or "low-angle" in a.lower(), a
    assert pb.camera_clause("auto", 0, "slow pan across the market", "narration", "").startswith("Camera: slow pan")
    assert "locked-off" in pb.camera_clause("static", 0) or "static" in pb.camera_clause("static", 0).lower()
    print("OK conversation prompt (stay): speaker stays put, faces Ada, no walking; the fall gets a dynamic camera; old modes unchanged")

    # walk-and-talk (default): they walk together, talk to each other, camera tracks, no bouncing/pacing
    _st.CONVERSATION_STAGING = "walk"
    w = pb.text_video_prompt("dialogue", "Kobi", "irritated", "Walking up and down the studio", CHARS[0].appearance_prompt, CHARS,
                             "a recording studio", "a dim studio", "Cinematic", 1, "auto", None, False, "slow pan", "", "", model_voice=False)
    wl = w.lower()
    assert "walk side by side" in wl and "ada" in wl and "tracking" in wl and "no bouncing" in wl and "no jumping" in wl, w
    assert "no walking" not in wl and "stands in one place" not in wl, w
    seated = pb.text_video_prompt("dialogue", "Kobi", "calm", "sits at the table", CHARS[0].appearance_prompt, CHARS,
                                  "a dinner table", "a family dinner", "Cinematic", 1, "auto", None, False, "", "", "", model_voice=False)
    assert "walk side by side" not in seated.lower() and "is sitting" in seated.lower(), seated
    fall = pb.text_video_prompt("dialogue", "Kobi", "terrified", "falls from the sky screaming", CHARS[0].appearance_prompt, CHARS,
                                "above the clouds", "open sky", "Cinematic", 1, "auto", None, False, "camera dives after him", "", "", model_voice=False)
    assert "walk side by side" not in fall.lower(), fall
    whole = pb.whole_scene_prompt(
        [{"kind": "dialogue", "speaker": "Kobi", "speech": "Come on, we are late.", "action": "", "expression": "", "voice_desc": ""},
         {"kind": "dialogue", "speaker": "Ada", "speech": "I am right behind you.", "action": "", "expression": "", "voice_desc": ""}],
        CHARS, "a market street", "busy street", "Cinematic", None, None, False)
    assert "walk together" in whole and "tracking shots" in whole and "no bouncing" in whole, whole
    _st.CONVERSATION_STAGING = "stay"
    whole2 = pb.whole_scene_prompt(
        [{"kind": "dialogue", "speaker": "Kobi", "speech": "Hi.", "action": "", "expression": "", "voice_desc": ""}],
        CHARS, "a market street", "busy street", "Cinematic", None, None, False)
    assert "walk together" not in whole2, whole2
    # ---- the scene's SITUATION decides the staging (Keto stuck in mud must look stuck, not like walking/running) ----
    from types import SimpleNamespace as _NS
    keto = _NS(name="Keto", appearance_prompt="a small cartoon fox with orange fur", description="")
    mara = _NS(name="Mara", appearance_prompt="a cartoon rabbit in a green scarf", description="")
    _st.CONVERSATION_STAGING = "walk"
    ctx_loc, ctx_vis = "a bush by the river", "Keto is stuck waist-deep in thick mud inside the bush, struggling to pull free"
    for act in ("", "gesturing while speaking", "walking around nervously"):
        st = pb.text_video_prompt("dialogue", "Keto", "scared", act, keto.appearance_prompt, [keto, mara], ctx_loc, ctx_vis,
                                  "Cartoon", 0, "auto", None, False, "slow push-in", "", "", model_voice=False,
                                  dialogue_text="Oh my god, I am stuck!")
        low = st.lower()
        assert "stuck fast" in low and "never walks or runs" in low and "pulls at their legs" in low, st
        assert "walk side by side" not in low and "tracking shot that moves alongside" not in low, st
        assert "handheld" in low, st   # struggle camera
    helper = pb.text_video_prompt("dialogue", "Mara", "worried", "", mara.appearance_prompt, [keto, mara], ctx_loc, ctx_vis,
                                  "Cartoon", 1, "auto", None, False, "", "", "", model_voice=False, dialogue_text="Hold on, I will pull you out!")
    assert "stays right beside keto" in helper.lower() and "does not walk away" in helper.lower(), helper
    assert "stuck fast" not in helper.lower(), helper
    # the line itself says it, even when the scene description never does
    said = pb.text_video_prompt("dialogue", "Keto", "scared", "", keto.appearance_prompt, [keto], "a bush", "a quiet bush",
                                "Cartoon", 0, "auto", None, False, "", "", "", model_voice=False, dialogue_text="Help, I'm stuck in the mud!")
    assert "stuck fast" in said.lower(), said
    # a specific action is performed as written, not replaced by walking
    kneel = pb.text_video_prompt("dialogue", "Mara", "calm", "kneels and opens the old box", mara.appearance_prompt, [keto, mara],
                                 "an attic", "dusty attic", "Cartoon", 0, "auto", None, False, "", "", "", model_voice=False)
    assert "kneels and opens the old box" in kneel and "walk side by side" not in kneel.lower(), kneel
    hide = pb.text_video_prompt("dialogue", "Keto", "afraid", "", keto.appearance_prompt, [keto], "a garden", "Keto hides behind the bush from the fox",
                                "Cartoon", 0, "auto", None, False, "", "", "", model_voice=False, dialogue_text="Shh, do not make a sound.")
    assert "crouched low and hidden" in hide.lower() and "walks" not in hide.lower(), hide
    sit = pb.text_video_prompt("dialogue", "Keto", "calm", "", keto.appearance_prompt, [keto, mara], "a kitchen", "Keto and Mara sit on a sofa",
                               "Cartoon", 0, "auto", None, False, "", "", "", model_voice=False, dialogue_text="Let us talk.")
    assert "is sitting" in sit.lower() and "walk side by side" not in sit.lower(), sit
    # other camera modes and image-first also show the situation
    free = pb.text_video_prompt("dialogue", "Keto", "scared", "", keto.appearance_prompt, [keto, mara], ctx_loc, ctx_vis,
                                "Cartoon", 0, "model", None, False, "", "", "", model_voice=False, dialogue_text="I am stuck!")
    assert "stuck fast" in free.lower(), free
    i2v = pb.video_prompt("dialogue", "Keto", "scared", "", "slow push-in", ctx_vis, 0, "auto", dialogue_text="I am stuck!")
    assert "stuck fast" in i2v.lower() and "walks steadily" not in i2v.lower(), i2v
    # whole-scene prompt: every beat shows the situation, no walking, struggle camera
    wb = pb.whole_scene_prompt(
        [{"kind": "dialogue", "speaker": "Keto", "speech": "Oh my god, I am stuck!", "action": "", "expression": "scared", "voice_desc": ""},
         {"kind": "dialogue", "speaker": "Mara", "speech": "Hold on, I am coming!", "action": "", "expression": "worried", "voice_desc": ""}],
        [keto, mara], ctx_loc, ctx_vis, "Cartoon", None, None, False)
    wl = wb.lower()
    assert "stuck fast" in wl and "stays right beside keto" in wl and "stays trapped in the same spot" in wl, wb
    assert "walk together" not in wl and "tracking shots that move with" not in wl, wb
    # generic-action detection
    for g in ("", "neutral", "gesturing while speaking", "walking up and down", "paces back and forth", "talks and nods"):
        assert pb.is_generic_action(g), g
    for sp in ("kneels", "pulls at his legs", "opens the box", "clenches fists"):
        assert not pb.is_generic_action(sp), sp
    print("OK situations: stuck-in-mud is shown as stuck (speaker) and as helping (other), hiding / sitting / specific actions are performed, walking only when nothing specific happens")
    _st.CONVERSATION_STAGING = "walk"
    print("OK walk-and-talk: they walk together and talk to each other, camera tracks, no bouncing; seated and action scenes are left alone")


class CutoffVideo:
    """A fake model whose voice needs 9.0 s: any shorter clip has the voice still going at its last instant."""
    NEED = 9.0

    def __init__(self):
        self.t2v = []

    def generate_text_clip(self, prompt, path, duration, seed=None, want_audio=None, resolution=None):
        self.t2v.append((prompt, round(duration, 2), seed))
        secs = round(duration, 2)
        voice = min(secs, self.NEED)
        af = (f"anoisesrc=d={secs}:c=pink:r=48000:a=0.002[room];anoisesrc=d={voice}:c=pink:r=48000:a=0.30,apad=whole_dur={secs}[v];"
              f"[room][v]amix=inputs=2:duration=longest:normalize=0[a]")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=960x544:r=24:d={secs}",
                        "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", path], check=True, capture_output=True)
        return path


class DrivenVideo:
    def __init__(self):
        self.calls = []

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False, resolution=None, audio_path=None):
        self.calls.append((duration, audio_path, os.path.exists(audio_path) if audio_path else False))
        secs = duration
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=768x512:r=24:d={secs}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True, capture_output=True)
        return path


def main():
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found"); sys.exit(1)
    ff, tmp = FFmpegService(), tempfile.mkdtemp()
    cast = VoiceCast(CHARS, "ng", "", texts=["studio"])
    settings.NARRATOR_ENABLED = False
    check_prompts()

    # ---- 1. a cut-off line is detected from the clip's audio and generated again, longer -------------------
    settings.AUDIO_MODE, settings.VIDEO_CAMERA_MOTION = "model", "auto"
    settings.SPEECH_COMPLETE_CHECK, settings.SPEECH_MAX_RETRIES, settings.SPEECH_RETRY_EXTRA_SECONDS = True, 2, 1.5
    settings.VIDEO_MAX_CLIP_SECONDS = 8.0
    vid = CutoffVideo()
    r = SceneRenderer(FakeImage(), vid, FakeTTS(), ff)
    r.render(scene(), CHARS, "Cinematic", cast, os.path.join(tmp, "cut"), "p1", FakeCtx(), video_mode="t2v")
    by_prompt = {}
    for prompt, dur, _ in vid.t2v:
        by_prompt.setdefault(prompt, []).append(dur)
    retried = [d for d in by_prompt.values() if len(d) > 1]
    assert retried, f"a clip that ends mid-voice must be generated again: {by_prompt}"
    for d in retried:
        assert d == sorted(d) and d[-1] > d[0], d
        assert d[-1] >= CutoffVideo.NEED - 0.01 or d[-1] >= settings.VIDEO_MAX_CLIP_SECONDS, d
    print("OK cut-off line: clip lengths tried per shot ->", [d for d in by_prompt.values()])

    settings.SPEECH_COMPLETE_CHECK = False
    vid2 = CutoffVideo()
    SceneRenderer(FakeImage(), vid2, FakeTTS(), ff).render(scene(), CHARS, "Cinematic", cast, os.path.join(tmp, "cut2"), "p1", FakeCtx(), video_mode="t2v")
    assert len(vid2.t2v) == len({p for p, _, _ in vid2.t2v}), "with the check off every clip is generated once"
    print("OK SPEECH_COMPLETE_CHECK=false generates each clip once")
    settings.SPEECH_COMPLETE_CHECK = True

    # ---- 2. audio-driven models: each shot gets its own slice of the voices, clip length = slice length ------
    settings.AUDIO_MODE, settings.VIDEO_AUDIO_DRIVEN = "voices", True
    tts, dv = FakeTTS(), DrivenVideo()
    res = SceneRenderer(FakeImage(), dv, tts, ff).render(scene(), CHARS, "Cinematic", cast, os.path.join(tmp, "driven"), "p1", FakeCtx(), video_mode="i2v")
    assert dv.calls and all(c[1] and c[2] for c in dv.calls), dv.calls
    assert len(tts.lines) == 2, "the pipeline's own voices still speak"
    assert ff.has_audio(res.final_path)
    wav_len = ff.get_duration(os.path.join(tmp, "driven", "voices.wav"))
    asked = sum(c[0] for c in dv.calls)
    assert abs(asked - wav_len) < 0.5, (asked, wav_len)
    print("OK audio-driven: %d shots, each sent its audio slice (total %.1fs of %.1fs voice track)" % (len(dv.calls), asked, wav_len))

    try:
        SceneRenderer(FakeImage(), dv, FakeTTS(), ff).render(scene(), CHARS, "Cinematic", cast, os.path.join(tmp, "driven_t2v"), "p1", FakeCtx(), video_mode="t2v")
        raise SystemExit("audio-driven + text-to-video must refuse")
    except RuntimeError as e:
        assert "image-first" in str(e)
    print("OK audio-driven + text-to-video gives a clear message instead of a bad render")
    print("\nALL DIALOGUE / SPEECH CHECKS PASSED")


if __name__ == "__main__":
    main()
