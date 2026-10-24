"""
Self-test for 'reuse footage': one generated clip per scene is cut into every shot, voices come from text-to-speech,
and editing the dialogue does not generate the footage again.   python scripts/selftest_reuse.py
"""
import copy
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, HERE)

from selftest_renderer import FakeCtx, FakeImage, FakeTTS  # noqa: E402
from selftest_model_voice import FakeModelVideo, scene  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.ffmpeg_service import FFmpegService  # noqa: E402
from app.services.scene_renderer import SceneRenderer  # noqa: E402
from app.services.voice_service import VoiceCast  # noqa: E402


def main():
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found"); sys.exit(1)
    ff = FFmpegService()
    root = tempfile.mkdtemp()
    chars = [NS(name="Kobi", description="a sound engineer, he is 30 years old",
                appearance_prompt="30-year-old Black man, short hair, dark hoodie",
                gender="male", age_group="adult", voice_id=None)]
    cast = VoiceCast(chars, "ng", "", texts=["Nollywood sound engineer"])
    settings.NARRATOR_ENABLED = False
    settings.AUDIO_MODE, settings.VIDEO_CAMERA_MOTION = "model", "model"   # reuse must still use text-to-speech

    for mode in ("t2v", "i2v"):
        proj = os.path.join(root, mode)
        sd = os.path.join(proj, "scene_1")
        os.makedirs(proj)
        tts, img, vid = FakeTTS(), FakeImage(), FakeModelVideo()
        r = SceneRenderer(img, vid, tts, ff)
        sc = scene()
        res = r.render(sc, chars, "Cinematic sci-fi", cast, sd, "p1", FakeCtx(), video_mode=mode,
                       resolution="640x352", scene_style="reuse")
        made = len(vid.t2v) if mode == "t2v" else len(vid.i2v)
        assert made == 1, f"{mode}: footage must be generated exactly once, got {made}"
        assert tts.lines, "voices must come from text-to-speech in reuse mode"
        assert res.shots >= 2, "the scene should still be cut into several shots"
        assert all(x == "640x352" for x in vid.seen_res), vid.seen_res
        total = ff.get_duration(res.final_path)
        assert total > 3, total
        assert ff.has_audio(res.final_path), "final scene must carry the voices"
        if mode == "i2v":
            assert len(img.prompts) == 1, "one keyframe only"
        print(f"OK {mode}: 1 footage clip -> {res.shots} shots, {total:.1f}s, {len(tts.lines)} voice lines")

        # Edit one line of dialogue: the footage must be reused, only the voices are redone.
        sc2 = copy.deepcopy(sc)
        sc2.dialogue_turns[2]["text"] = "Now it finally works, thank goodness"
        tts2, img2, vid2 = FakeTTS(), FakeImage(), FakeModelVideo()
        r2 = SceneRenderer(img2, vid2, tts2, ff)
        res2 = r2.render(sc2, chars, "Cinematic sci-fi", cast, sd, "p1", FakeCtx(), video_mode=mode,
                         resolution="640x352", scene_style="reuse")
        assert not vid2.t2v and not vid2.i2v and not img2.prompts, "edited dialogue must NOT regenerate the footage"
        assert tts2.lines, "the new line must be recorded"
        assert ff.get_duration(res2.final_path) > 3
        print(f"OK {mode}: dialogue edit reused the footage, re-recorded {len(tts2.lines)} line(s)")

        # A different resolution is a different picture: footage is generated again.
        tts3, img3, vid3 = FakeTTS(), FakeImage(), FakeModelVideo()
        SceneRenderer(img3, vid3, tts3, ff).render(sc2, chars, "Cinematic sci-fi", cast, sd, "p1", FakeCtx(), video_mode=mode,
                                                   resolution="512x288", scene_style="reuse")
        assert (vid3.t2v or vid3.i2v), "a new resolution must render new footage"
        print(f"OK {mode}: new resolution renders new footage")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL REUSE-FOOTAGE CHECKS PASSED")


if __name__ == "__main__":
    main()
