"""
Self-test for "finished GPU clips are never thrown away for nothing":
  1. a settings change that does not touch the picture keeps the finished clips (no GPU call on the next run);
  2. a change to the picture itself starts over (the clips would look different);
  3. a scene that had to be split once is split the same way next time at once (no render-find-out-split again);
  4. a clip that took too long to render is kept, never rendered again because its last word seems clipped.
Needs only ffmpeg + Pillow:   python scripts/selftest_no_waste.py
"""
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
from app.core.config import settings  # noqa: E402
from selftest_complete_lines import SlowVoiceVideo, scene, render, ff, CHARS  # noqa: E402
from selftest_renderer import FakeTTS  # noqa: E402


def main():
    settings.AUDIO_MODE = "model"; settings.SCENE_MODE = "whole"; settings.WHOLE_SCENE_MAX_SECONDS = 30.0
    settings.VIDEO_MAX_CLIP_SECONDS = 10.0; settings.SPEECH_COMPLETE_CHECK = True; settings.SPEECH_FALLBACK_TTS = True
    settings.SPEECH_RETRY_MAX_RENDER_SECONDS = 900
    tmp = tempfile.mkdtemp(prefix="nowaste_")
    lines = [("Ada", "An apple grows on a tree."), ("Kobi", "Why is it red today?")]
    try:
        # ---- 1. non-picture settings change -> clips kept
        vid = SlowVoiceVideo(wps=1.6, limit_s=8.04)
        render(vid, scene(lines), tmp, "a")
        first = len(vid.calls)
        assert first >= 1
        settings.SPEECH_TAIL_MARGIN_DB = 13.0          # part of the full fingerprint, not of the picture
        render(vid, scene(lines), tmp, "a")
        assert len(vid.calls) == first, ("finished clips must be reused", first, len(vid.calls))
        print("OK 1: settings changed, picture not -> %d finished clip(s) reused, 0 new GPU calls" % first)
        settings.SPEECH_TAIL_MARGIN_DB = 14.0

        # ---- 2. the picture changes -> start over
        sc = scene(lines); sc.visual_description = "A dark cellar with candles"
        render(vid, sc, tmp, "a")
        assert len(vid.calls) > first, "a different picture must be rendered again"
        print("OK 2: the scene's look changed -> rendered again (%d new call(s))" % (len(vid.calls) - first))

        # ---- 3. a split learned once is applied at once next time
        vid = SlowVoiceVideo(wps=0.9, limit_s=8.04)      # slow voice: the two lines do not fit one clip
        render(vid, scene(lines), tmp, "b")
        n1 = len(vid.calls)
        assert os.path.exists(os.path.join(tmp, "b", "split_memory.json")), "what was learned must be saved"
        settings.SPEECH_TAIL_MARGIN_DB = 13.0
        render(vid, scene(lines), tmp, "b")
        assert len(vid.calls) == n1, ("a resume must not repeat the render-find-out-split cycle", n1, len(vid.calls))
        print("OK 3: split found after %d generation(s); the next run reused the final clips with 0 new GPU calls" % n1)
        # and a fresh run in a NEW folder that still has the memory: no big clip is attempted first
        shutil.copytree(os.path.join(tmp, "b"), os.path.join(tmp, "c"))
        for f in os.listdir(os.path.join(tmp, "c")):
            if f.endswith(("_raw.mp4", ".key")):
                os.remove(os.path.join(tmp, "c", f))
        vid2 = SlowVoiceVideo(wps=0.9, limit_s=8.04)
        settings.SPEECH_TAIL_MARGIN_DB = 12.0
        render(vid2, scene(lines), tmp, "c")
        assert len(vid2.calls) < n1, ("with the memory the discarded big clip is never rendered", n1, len(vid2.calls))
        print("OK 3b: clips lost, memory kept -> %d generation(s) instead of %d (the too-big clip is not tried again)" % (len(vid2.calls), n1))
        settings.SPEECH_TAIL_MARGIN_DB = 14.0

        # ---- 4. slow clip is kept, not regenerated
        settings.SPEECH_RETRY_MAX_RENDER_SECONDS = 0.0001      # every clip counts as 'slow'
        vid = SlowVoiceVideo(wps=1.4, limit_s=8.04, never_finishes=True)
        render(vid, scene([("Ada", "An apple grows on a tree.")]), tmp, "d", tts=FakeTTS())
        assert len(vid.calls) == 1, ("a slow clip must not be rendered again", vid.calls)
        print("OK 4: a slow clip whose last word seems clipped is kept (1 GPU call; normally it would retry up to %d times)" % settings.SPEECH_MAX_RETRIES)
        print("ALL NO-WASTE CHECKS PASSED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
