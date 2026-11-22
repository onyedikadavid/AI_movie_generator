"""Self-test: music bed (looped, ducked under speech) and keep-story prompt. Needs ffmpeg."""
import asyncio, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import importlib.util, types
for mod in ("httpx", "sqlalchemy"):
    try:
        __import__(mod)
    except ImportError:                  # test machines without these: the model calls are faked anyway
        sys.modules[mod] = types.SimpleNamespace(AsyncClient=object, Client=object, Timeout=object, ConnectError=Exception)
# Load only what the script writer needs, so the database layer is not imported.
pkg = types.ModuleType("app.schemas"); pkg.__path__ = [os.path.join(HERE, "..", "app", "schemas")]; sys.modules["app.schemas"] = pkg
spec = importlib.util.spec_from_file_location("app.schemas.script", os.path.join(HERE, "..", "app", "schemas", "script.py"))
m = importlib.util.module_from_spec(spec); sys.modules["app.schemas.script"] = m; spec.loader.exec_module(m)
for name, attrs in {"app.services.agent_service": {"AgentService": object},
                    "app.services.dynamic_config": {"resolve_url": lambda *a, **k: "", "KEY_OLLAMA_URL": "k"},
                    "app.services.http_retry": {"post_with_retry": None}}.items():
    sys.modules[name] = types.SimpleNamespace(**attrs)
from app.services.llm_service import LLMService

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.config import settings
from app.services import music
from app.services.ffmpeg_service import FFmpegService


def sh(*a):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)


def level(path, t0, t1):
    out = subprocess.run(["ffmpeg", "-ss", str(t0), "-t", str(t1 - t0), "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float([l for l in out.splitlines() if "mean_volume" in l][0].split(":")[1].split()[0].replace("dB", ""))


def main():
    tmp = tempfile.mkdtemp()
    try:
        settings.STORAGE_DIR = tmp
        settings.MUSIC_DIR = ""
        assert music.pick_track("p1") is None
        os.makedirs(os.path.join(tmp, "music"))
        sh("-f", "lavfi", "-i", "sine=f=220:d=4", os.path.join(tmp, "music", "sad_piano.wav"))
        sh("-f", "lavfi", "-i", "sine=f=330:d=4", os.path.join(tmp, "music", "happy_ukulele.wav"))
        assert music.pick_track("p1", "A sad, quiet tone").endswith("sad_piano.wav")
        assert music.pick_track("p1", "happy").endswith("happy_ukulele.wav")
        assert music.pick_track("p1") == music.pick_track("p1")
        print("OK 1: track picked by tone, stable otherwise, none when the folder is empty")
        # a 20 s film: speech (noise) only in seconds 0-8, quiet after
        sh("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=20", "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.5,apad=whole_dur=20",
           "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(tmp, "film.mp4"))
        ff = FFmpegService()
        out = ff.add_music_bed(os.path.join(tmp, "film.mp4"), os.path.join(tmp, "music", "sad_piano.wav"), os.path.join(tmp, "out.mp4"), 0.5, True)
        d = ff.get_duration(out)
        assert abs(d - 20) < 0.6, d
        quiet = level(out, 12, 16)
        assert quiet > -45, f"the music must be audible after the speech ({quiet} dB) although the track is only 4 s long (looped)"
        out2 = ff.add_music_bed(os.path.join(tmp, "film.mp4"), os.path.join(tmp, "music", "sad_piano.wav"), os.path.join(tmp, "out2.mp4"), 0.5, False)
        assert level(out2, 1, 7) >= level(out, 1, 7) - 0.5, "ducking must not make the speech part louder"
        print(f"OK 2: music looped to the film's length ({d:.1f}s), audible after the speech ({quiet:.0f} dB)")

        llm = LLMService.__new__(LLMService)
        seen = {}

        async def gen(prompt):
            seen["p"] = prompt
            raise RuntimeError("stop")
        llm._generate = gen
        called = {"bible": 0}

        async def bible(*a, **k):
            called["bible"] += 1
            return {}
        llm._story_bible = bible
        story = "Michael walks to the village. He says: \"Good morning, Obinna.\" Then it rains."
        try:
            asyncio.run(llm.generate_script_structure(story, scene_count=2, scene_seconds=120, keep_story=True))
        except RuntimeError:
            pass
        assert called["bible"] == 0, "keep_story must not ask the writer to rewrite the story"
        assert "KEEP IT EXACTLY" in seen["p"] and "Good morning, Obinna" in seen["p"], seen["p"][:300]
        called["bible"] = 0
        try:
            asyncio.run(llm.generate_script_structure(story, scene_count=2, scene_seconds=120, keep_story=False))
        except RuntimeError:
            pass
        assert called["bible"] == 1
        print("OK 3: keep_story skips the rewrite and gives the story to the scene writer word for word")
        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
