"""
Self-test: "Whole scene" is ONE clip per scene.
  1. long dialogue + Auto seconds -> exactly one clip (the old behaviour cut it at 8 s into several);
  2. WHOLE_SCENE_STRICT=false still gives the old split (the switch works);
  3. a scene asked to be longer than the server's longest clip -> the FEWEST clips (ceil(want/ceiling)), evenly shared;
  4. a scene asked for N seconds that fits one clip -> one clip of about N seconds.
Needs only ffmpeg + Pillow:   python scripts/selftest_strict_whole.py
"""
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))
from app.core.config import settings  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402
from selftest_complete_lines import SlowVoiceVideo, scene, ff, CHARS  # noqa: E402
from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402


def go(vid, sc, tmp, name, seconds=None):
    r = SceneRenderer(FakeImage(), vid, FakeTTS(), ff)
    return r.render(sc, CHARS, "Cinematic", VoiceCast(CHARS), os.path.join(tmp, name), "p1", FakeCtx(),
                    video_mode="t2v", scene_seconds=seconds)


def main():
    settings.AUDIO_MODE = "model"; settings.SCENE_MODE = "whole"; settings.WHOLE_SCENE_MAX_SECONDS = 8.0
    settings.VIDEO_MAX_CLIP_SECONDS = 20.0; settings.SPEECH_COMPLETE_CHECK = False
    # unit: how many clips a long scene needs
    B = SceneRenderer._balance_parts
    assert len(B([[1, 2, 3, 4]], 20.0, 120.0)) == 6, "120 s at a 20 s ceiling = 6 clips"
    assert len(B([[1]], 20.0, 20.0)) == 1 and len(B([[1]], 20.0, 8.0)) == 1
    assert len(B([[1, 2]], 20.0, 41.0)) == 3
    print("OK unit: clips needed = ceil(want / ceiling), lines shared out, empty carry-on clips only when needed")

    tmp = tempfile.mkdtemp(prefix="strict_")
    lines = [("Ada", "Good morning class, today we learn where an apple comes from."),
             ("Kobi", "I think apples come from the shop on our street, miss."),
             ("Ada", "Not quite, Kobi. Every apple grows on a tree.")]
    try:
        settings.WHOLE_SCENE_STRICT = True
        vid = SlowVoiceVideo(wps=3.0, limit_s=20.04)
        go(vid, scene(lines), tmp, "a")
        assert len(vid.calls) == 1, ("ONE clip for the whole scene", len(vid.calls))
        print("OK 1: three long lines, Auto seconds -> ONE clip (%.1f s asked)" % vid.calls[0][1])

        settings.WHOLE_SCENE_STRICT = False
        vid2 = SlowVoiceVideo(wps=3.0, limit_s=20.04)
        go(vid2, scene(lines), tmp, "b")
        assert len(vid2.calls) >= 2, "old behaviour (switch off) still splits at 8 s"
        print("OK 2: WHOLE_SCENE_STRICT=false -> old behaviour, %d clips" % len(vid2.calls))
        settings.WHOLE_SCENE_STRICT = True

        vid3 = SlowVoiceVideo(wps=3.0, limit_s=20.04)
        go(vid3, scene(lines[:1]), tmp, "c", seconds=45)
        assert len(vid3.calls) == 3, ("45 s at a 20 s ceiling = 3 clips", len(vid3.calls))
        assert all(abs(c[1] - 15.0) < 0.5 for c in vid3.calls), vid3.calls
        print("OK 3: 45 s asked, 20 s ceiling -> 3 clips of ~15 s: %s" % [c[1] for c in vid3.calls])

        vid4 = SlowVoiceVideo(wps=3.0, limit_s=20.04)
        go(vid4, scene(lines[:1]), tmp, "d", seconds=14)
        assert len(vid4.calls) == 1 and abs(vid4.calls[0][1] - 14.0) < 0.6, vid4.calls
        print("OK 4: 14 s asked -> one clip of %.1f s" % vid4.calls[0][1])
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
