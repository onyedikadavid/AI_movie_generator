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
    p = pb.text_video_prompt("dialogue", "Kobi", "irritated", "Walking up and down the studio", CHARS[0].appearance_prompt, CHARS,
                             "a recording studio", "a dim studio", "Cinematic", 1, "auto", None, False, "slow pan", "", "", model_voice=False)
    assert "Over-the-shoulder" in p and "facing Ada" in p and "no walking" in p and "Ada" in p, p
    assert "Walking up and down" not in p and "tracking" not in p.lower() and "arc" not in p.lower(), p
    a = pb.text_video_prompt("dialogue", "Kobi", "terrified", "falls from the sky screaming", CHARS[0].appearance_prompt, CHARS,
                             "above the clouds", "open sky", "Cinematic", 1, "auto", None, False, "camera dives after him", "", "", model_voice=False)
    assert "no walking" not in a and "dynamic" in a.lower() or "sweeping" in a.lower() or "handheld" in a.lower() or "low-angle" in a.lower(), a
    assert pb.camera_clause("auto", 0, "slow pan across the market", "narration", "").startswith("Camera: slow pan")
    assert "locked-off" in pb.camera_clause("static", 0) or "static" in pb.camera_clause("static", 0).lower()
    print("OK conversation prompt: speaker stays put, faces Ada, no walking; the fall gets a dynamic camera; old modes unchanged")


class CutoffVideo:
    """A fake model whose voice needs 5.5 s: any shorter clip has the voice still going at its last instant."""
    NEED = 5.5

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
        assert d[-1] >= CutoffVideo.NEED or len(d) == settings.SPEECH_MAX_RETRIES + 1, d   # finished, or retries used up
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
