"""Self-test of the script writer (no Ollama needed): story analysis first, hook + ending rules in the prompt,
one automatic fix-up pass for a weak script, and the story beat reaching the dialogue writer."""
import asyncio, json, os, sys
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

calls = []
BIBLE = {"logline": "A boy climbs a tree, falls, is helped, and flies to another world", "main_character": "Ayo", "problem": "the fall",
         "turning_point": "he starts to fly", "ending": "he lands in a new world", "message": "wonder", "hook": "a boy dangling from a high branch",
         "story": "A boy dangles from a high branch. He falls. A stranger helps him. He flies. He lands in another world."}

class FakeAgent:
    async def simulate_monologue(self, solo, goal, lines=2, kids=False):
        return [{"speaker": solo["name"], "text": "Whoa.", "expression": "awed", "action": ""}]
    async def simulate_interaction(self, a, b, goal, turns=4, kids=False):
        return [{"speaker": a["name"], "text": "Hold on!", "expression": "scared", "action": ""}]

async def main():
    n = 12
    svc = object.__new__(LLMService); svc.agent_service = FakeAgent()
    async def fake(prompt):
        calls.append(prompt)
        if "\"story\"" in prompt and "Return JSON with exactly these keys" in prompt:
            return json.dumps(BIBLE)
        if "Write beats" in prompt:
            a = int(prompt.split("Write beats ")[1].split(" to ")[0]); b = int(prompt.split(" to ")[1].split(" of ")[0])
            return json.dumps({"beats": [f"beat {i}" for i in range(a, b + 1)]})
        # scene batch: honour the requested numbers
        start = int(prompt.split("Write scenes ")[1].split(" to ")[0]); end = int(prompt.split(" to ")[1].split(".")[0])
        sc = [{"scene_number": i, "story_beat": f"beat {i}", "continues_previous": i % 4 != 0, "location": "tree" if i < 6 else "other world",
               "visual_description": f"v{i}", "narration_text": "n", "characters_present": ["Ayo"], "image_prompt": "i", "motion_prompt": "m",
               "duration_seconds": 8} for i in range(start, end + 1)]
        out = {"scenes": sc}
        if start == 1:
            out.update({"title": "Ayo Flies", "genre": "fantasy", "tone": "wonder", "visual_style": "cinematic",
                        "characters": [{"name": "Ayo", "description": "a boy", "appearance_prompt": "10yo boy", "gender": "male", "age_group": "child"}]})
        return json.dumps(out)
    svc._generate = fake
    out = await svc.generate_script_structure("A boy climbs a tree, falls, is helped and flies to another world",
                                              scene_count=n, scene_seconds=8, scene_style="film")
    nums = [s.scene_number for s in out.scenes]
    assert nums == list(range(1, n + 1)), nums
    assert out.scenes[0].continues_previous is False
    assert any(s.continues_previous for s in out.scenes[1:]) and not out.scenes[3].continues_previous, [s.continues_previous for s in out.scenes]
    assert all(s.duration_seconds == 8 for s in out.scenes)
    assert out.title == "Ayo Flies" and out.characters[0].name == "Ayo" and out.story.startswith("A boy dangles")
    assert all(s.dialogue_turns for s in out.scenes), "dialogue must be written for every scene"
    batches = [c for c in calls if "Write scenes" in c]
    assert len(batches) == 3 and "beat 6" in batches[1] and "PREVIOUS SCENES" in batches[1] and "CHARACTERS (names and looks are FIXED" in batches[1]
    assert "Scene 5" in batches[1], "the recap of earlier scenes must reach the next batch"
    print(f"OK film: {n} scenes written in {len(batches)} batches, chained flags kept, dialogue everywhere")

    # a model that returns too few beats / scenes must still end with exactly n scenes
    calls.clear()
    short = {"n": 0}
    async def flaky(prompt):
        if "Write beats" in prompt:
            return json.dumps({"beats": ["only one"]})
        return await fake(prompt)
    svc._generate = flaky
    out = await svc.generate_script_structure("x", scene_count=7, scene_seconds=8, scene_style="film")
    assert [s.scene_number for s in out.scenes] == list(range(1, 8)), [s.scene_number for s in out.scenes]
    print("OK film: missing outline beats are filled, still exactly 7 scenes")

asyncio.run(main())
print("\nALL FILM-SCRIPT CHECKS PASSED")
