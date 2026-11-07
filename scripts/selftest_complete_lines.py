"""
Self-test for "the words a character says are never cut off": a spoken line is never shortened or cut mid-sentence,
a clip whose voice is still going at its end is generated again / split, and a line the video model keeps cutting off is
spoken in full by the pipeline's own voice. Needs only ffmpeg + Pillow.

    python scripts/selftest_complete_lines.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from app.core.config import settings  # noqa: E402
from app.services.ffmpeg_service import FFmpegService  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast, limit_sentences, speech_seconds  # noqa: E402
from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402

CHARS = [NS(name="Ada", description="a teacher", appearance_prompt="a woman in a red dress", gender="female", age_group="adult", voice_id=None),
         NS(name="Kobi", description="a boy", appearance_prompt="a boy in a green jacket", gender="male", age_group="child", voice_id=None)]
ff = FFmpegService()


def scene(lines):
    return NS(scene_number=1, duration_seconds=8, location="a classroom", visual_description="A bright classroom",
              image_prompt="a classroom", motion_prompt="slow push-in", sound_design="quiet room tone", characters_present=["Ada", "Kobi"],
              narration_text="", dialogue_turns=[{"speaker": s, "text": t, "action": "", "expression": "calm"} for s, t in lines])


class SlowVoiceVideo:
    """Fake video model with a SLOW voice (`wps` words per second, starts 0.7 s in) and a hard clip limit (`limit_s`).
    The voice runs to the very end of a clip that is too short - exactly what a cut-off line sounds like."""

    def __init__(self, wps=1.4, limit_s=8.04, never_finishes=False):
        self.wps, self.limit_s, self.never = wps, limit_s, never_finishes
        self.calls = []

    def server_max_seconds(self):
        return self.limit_s

    def _make(self, prompt, path, duration):
        words = sum(len(q.split()) for q in re.findall(r'"([^"]+)"', prompt))
        secs = (8 * int(min(duration, self.limit_s) * 24 // 8) + 1) / 24
        need = 0.7 + words / self.wps
        voice = secs if self.never else min(secs - 0.0, need)
        voice_len = max(voice - 0.7, 0.1)
        af = (f"anoisesrc=d={secs}:c=pink:r=48000:a=0.002[room];anoisesrc=d={voice_len}:c=pink:r=48000:a=0.30,"
              f"adelay=700|700,apad=whole_dur={secs}[v];[room][v]amix=inputs=2:duration=longest:normalize=0[a]")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=960x544:r=24:d={secs}",
                        "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-t", f"{secs}", path], check=True, capture_output=True)

    def generate_text_clip(self, prompt, path, duration, seed=None, want_audio=None, resolution=None):
        self.calls.append((prompt, round(duration, 2)))
        self._make(prompt, path, duration)
        return path

    def generate_clip(self, image, path, prompt, duration, seed=None, want_audio=False, resolution=None, audio_path=None):
        self.calls.append((prompt, round(duration, 2)))
        self._make(prompt, path, duration)
        return path


def norm(text):
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def spoken_words(calls):
    """Everything the model was asked to say, in order. Compared without punctuation: a line that had to be split gets a
    comma where the voice carries on into the next clip."""
    out = []
    for prompt, _ in calls:
        out += re.findall(r'"([^"]+)"', prompt)
    return norm(" ".join(out))


def render(vid, sc, tmp, name, tts=None, style_scene=None):
    r = SceneRenderer(FakeImage(), vid, tts or FakeTTS(), ff)
    cast = VoiceCast(CHARS)
    return r.render(sc, CHARS, "Cinematic", cast, os.path.join(tmp, name), "p1", FakeCtx(), video_mode="t2v")


def main():
    settings.AUDIO_MODE = "model"
    settings.SCENE_MODE = "whole"
    settings.WHOLE_SCENE_MAX_SECONDS = 30.0
    settings.VIDEO_MAX_CLIP_SECONDS = 10.0
    settings.SPEECH_COMPLETE_CHECK = True
    settings.SPEECH_FALLBACK_TTS = True
    tmp = tempfile.mkdtemp(prefix="complete_")
    try:
        # ---- 0. the limiter never cuts a sentence in half
        apple = "This fruit is gotten from an apple tree."
        assert limit_sentences(apple, 5) == apple, "no boundary -> the line stays whole"
        two = "An apple is a fruit. It grows on trees that need sun and water to bear fruit every year."
        assert limit_sentences(two, 8) == "An apple is a fruit.", limit_sentences(two, 8)
        assert limit_sentences("Hello there, my friend, how are you doing today", 5).endswith("."), "clause cut ends with a full stop"
        print("OK limit_sentences: whole sentences / clauses only, 'this fruit is gotten from an apple tree' can never become '... gotten from'")

        # ---- 1. the apple line, slow voice, 8 s server: complete in ONE clip, not shortened
        vid = SlowVoiceVideo(wps=1.4, limit_s=8.04)
        render(vid, scene([("Ada", apple)]), tmp, "apple")
        got = spoken_words(vid.calls)
        assert norm(apple) in got, got
        final = [c for c in os.listdir(os.path.join(tmp, "apple")) if c.endswith("_raw.mp4")]
        active = ff.tail_speech_active(os.path.join(tmp, "apple", sorted(final)[-1]))[0]
        assert not active, "the last clip must end after the voice finished"
        print("OK apple line: spoken in full (clip lengths asked: %s)" % [d for _, d in vid.calls])

        # ---- 2. a long scene: nothing shortened, split over several clips, each within the server limit
        lines = [("Ada", "An apple is a fruit that grows on an apple tree."), ("Kobi", "Does it grow in winter or in summer?"),
                 ("Ada", "It flowers in spring and the fruit is ready to pick in the autumn."), ("Kobi", "Can I eat it straight from the tree?"),
                 ("Ada", "Yes, wash it first, and then enjoy every juicy bite of it.")]
        vid = SlowVoiceVideo(wps=1.6, limit_s=8.04)
        render(vid, scene(lines), tmp, "long")
        got = spoken_words(vid.calls)
        pos = -1
        for _, t in lines:
            k = got.find(norm(t), pos + 1)
            assert k > pos, ("line cut or out of order", t, got)
            pos = k
        assert len(vid.calls) >= 2 and all(d <= 8.05 for _, d in vid.calls), vid.calls
        print("OK long scene: %d clips, all %d lines complete and in order, none asked longer than the server limit" % (len(vid.calls), len(lines)))

        # ---- 3. voice slower than expected + server cap: the last line of a clip is cut -> scene is split, line completes
        vid = SlowVoiceVideo(wps=0.9, limit_s=8.04)
        render(vid, scene([("Ada", "An apple grows on a tree."), ("Kobi", "Why is it red today?")]), tmp, "slow")
        got = spoken_words(vid.calls)
        assert norm("An apple grows on a tree") in got and norm("Why is it red today") in got, got
        print("OK very slow voice: still complete (%d generations: %s)" % (len(vid.calls), [d for _, d in vid.calls]))

        # ---- 4. a model that never finishes: the full line is spoken by the pipeline's voice instead
        tts = FakeTTS()
        vid = SlowVoiceVideo(wps=1.4, limit_s=8.04, never_finishes=True)
        res = render(vid, scene([("Ada", apple)]), tmp, "never", tts=tts)
        said = norm(" ".join(t for _, _, t in tts.lines))
        assert norm(apple) in said, ("fallback voice must say the whole line", tts.lines)
        assert res.duration > 2.0
        print("OK model that keeps cutting the line off -> pipeline voice says it in full:", [t for _, _, t in tts.lines])

        # ---- 5. shot-by-shot mode gets the same protection
        settings.SCENE_MODE = "shots"
        tts = FakeTTS()
        vid = SlowVoiceVideo(wps=1.4, limit_s=8.04, never_finishes=True)
        r = SceneRenderer(FakeImage(), vid, tts, ff)
        r.render(scene([("Ada", apple)]), CHARS, "Cinematic", VoiceCast(CHARS), os.path.join(tmp, "shots"), "p1", FakeCtx(), video_mode="t2v", scene_style="shots")
        said = norm(" ".join(t for _, _, t in tts.lines))
        assert norm(apple) in said, tts.lines
        print("OK shot-by-shot: a line the model keeps cutting off is spoken in full by the pipeline voice")
        print("\nALL COMPLETE-LINE CHECKS PASSED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
