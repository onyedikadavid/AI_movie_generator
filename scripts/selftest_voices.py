"""
Self-test (v2.35): voices.
 - edge: the newer MULTILINGUAL neural voices lead the pools and one narrates (not Christopher); Nigerian stories keep accents first
 - kokoro: Kokoro voice ids, an old edge voice id on a character is replaced, narrator is a Kokoro voice
 - kokoro engine: speaks sentence by sentence through the model, maps rate -> speed, writes an mp3 (a stand-in model is used here)
 - a failing Kokoro stops with a clear message, or falls back to edge when TTS_ALLOW_FALLBACK is on
Needs ffmpeg + numpy:   python scripts/selftest_voices.py
"""
import os, shutil, subprocess, sys, tempfile, types
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import voice_service as vs  # noqa: E402
from app.services.tts_service import TTSService, TTSError  # noqa: E402


def chars():
    return [NS(name="Ada", description="a teacher", appearance_prompt="woman", gender="female", age_group="adult", voice_id=None),
            NS(name="Kobi", description="a boy", appearance_prompt="boy", gender="male", age_group="adult", voice_id=None),
            NS(name="Tunde", description="a man", appearance_prompt="man", gender="male", age_group="adult", voice_id=None),
            NS(name="Sara", description="a woman", appearance_prompt="woman", gender="female", age_group="adult", voice_id=None)]


class FakeKokoro:
    calls = []

    def __init__(self, *a):
        pass

    def create(self, text, voice, speed, lang):
        FakeKokoro.calls.append((text, voice, round(speed, 2), lang))
        n = int(24000 * max(0.4, len(text.split()) * 0.3))
        t = np.arange(n) / 24000.0
        return (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 24000


def main():
    tmp = tempfile.mkdtemp(prefix="voices_")
    try:
        settings.STORAGE_DIR = tmp
        # ---- edge, multilingual
        settings.TTS_ENGINE, settings.TTS_EDGE_MULTILINGUAL = "edge", True
        cast = vs.VoiceCast(chars(), region="us")
        assert cast.narrator_voice == "en-US-AndrewMultilingualNeural", cast.narrator_voice
        got = {m.name: m.voice_id for m in cast.members}
        assert got["Ada"] == "en-US-AvaMultilingualNeural" and got["Sara"] == "en-US-EmmaMultilingualNeural", got
        assert got["Kobi"] == "en-US-BrianMultilingualNeural" and len(set(got.values())) == 4, got   # Andrew narrates, so Brian is the first man
        assert "Christopher" not in str(vs.narrator_profile("us")), vs.narrator_profile("us")
        ng = vs.VoiceCast(chars(), region="ng")
        gn = {m.name: m.voice_id for m in ng.members}
        assert gn["Kobi"] == "en-NG-AbeoNeural" and gn["Ada"] == "en-NG-EzinneNeural", gn          # accents first for Nigerian stories
        settings.TTS_EDGE_MULTILINGUAL = False
        old = vs.VoiceCast(chars(), region="us")
        assert old.members[0].voice_id == "en-US-JennyNeural" and old.narrator_voice == "en-US-ChristopherNeural"
        settings.TTS_EDGE_MULTILINGUAL = True
        print("OK 1: edge pools lead with the multilingual voices; narrator = Andrew Multilingual; Nigerian accents kept first")

        # ---- kokoro casting
        settings.TTS_ENGINE = "kokoro"
        cs = chars()
        cs[0].voice_id = "en-US-JennyNeural"          # a voice stored by an earlier edge run
        kc = vs.VoiceCast(cs, region="us")
        kv = {m.name: m.voice_id for m in kc.members}
        assert all(vs.is_kokoro_voice(v) for v in kv.values()), kv
        assert len(set(kv.values())) == 4 and kc.narrator_voice == "bm_fable" and kc.narrator_voice not in kv.values(), (kv, kc.narrator_voice)
        assert vs.narrator_profile("us", "en-US-AndrewMultilingualNeural").voice == "bm_fable"   # an edge override is ignored for Kokoro
        assert kc.write_back(cs) and vs.is_kokoro_voice(cs[0].voice_id), cs[0].voice_id           # the stale edge id is replaced on the row
        print(f"OK 2: kokoro casting {kv}, narrator {kc.narrator_voice}")

        # ---- kokoro engine
        mod = types.ModuleType("kokoro_onnx"); mod.Kokoro = FakeKokoro
        sys.modules["kokoro_onnx"] = mod
        kd = os.path.join(tmp, "models", "kokoro"); os.makedirs(kd)
        for n in (settings.KOKORO_MODEL_FILE, settings.KOKORO_VOICES_FILE):
            open(os.path.join(kd, n), "wb").write(b"x" * 1_200_000)               # "already downloaded"
        TTSService._kokoro_model = None
        tts = TTSService()
        prof = vs.VoiceProfile(voice="bf_emma", gender="female", rate=10, pitch=4, volume=-10, label="Ada")
        out = os.path.join(tmp, "a.mp3")
        tts.synthesize("Good morning class. Today we learn about apples! Are you ready?", prof, out, "Ada")
        assert os.path.getsize(out) > 2000 and len(FakeKokoro.calls) == 3, FakeKokoro.calls
        assert FakeKokoro.calls[0][1:] == ("bf_emma", 1.1, "en-gb"), FakeKokoro.calls[0]
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out], capture_output=True, text=True).stdout)
        assert dur > 2.0, dur
        assert "kokoro" in tts.engines_used
        print(f"OK 3: kokoro speaks sentence by sentence ({len(FakeKokoro.calls)} pieces), rate 10% -> speed 1.1, {dur:.1f}s mp3")

        # ---- failure paths
        class Broken(FakeKokoro):
            def create(self, *a, **k):
                raise RuntimeError("model crashed")
        mod.Kokoro = Broken; TTSService._kokoro_model = None
        settings.TTS_ALLOW_FALLBACK = False
        try:
            TTSService().synthesize("Hello there.", prof, os.path.join(tmp, "b.mp3"), "Ada")
            raise AssertionError("must stop")
        except TTSError as e:
            assert "kokoro-onnx" in str(e) and "Resume" in str(e), e
        called = {}
        t2 = TTSService()
        def fake_edge(text, profile, path):
            called["voice"] = profile.voice
            open(path, "wb").write(b"m" * 1000)
        t2._edge = fake_edge
        settings.TTS_ALLOW_FALLBACK = True
        t2.synthesize("Hello there.", prof, os.path.join(tmp, "c.mp3"), "Ada")
        assert called["voice"] == "en-US-JennyNeural", called
        print("OK 4: a failing Kokoro stops with a clear message; with TTS_ALLOW_FALLBACK it falls back to an edge voice")
        print("ALL PASS")
    finally:
        settings.TTS_ENGINE = "edge"; settings.TTS_ALLOW_FALLBACK = False
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
