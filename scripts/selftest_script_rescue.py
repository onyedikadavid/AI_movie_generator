"""Self-test (v2.35.2): a script the model got half wrong (scenes only, screenplay-style keys, no title, no characters) is
filled in instead of failing the run."""
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


STORY = """Emma was a small child sitting on the kitchen floor.

Emma: Waaaahhh... Mama!
Sarah: Oh, sweetie, what's wrong, huh?

Sarah picked her up and held her close."""

BAD1 = {"scenes": [{"episode": "1", "scene": "INT. KITCHEN - DAY"}], "story_outline": None}
BAD2 = {"scenes": [{"scene": "INT. KITCHEN - DAY", "action": "Emma cries on the floor."}], "story_outline": None}
CHARS = {"characters": [{"name": "Emma", "description": "a toddler", "appearance_prompt": "toddler girl, curly hair, yellow dress", "gender": "female", "age_group": "child"},
                        {"name": "Sarah", "description": "her mother", "appearance_prompt": "woman, brown hair, blue apron", "gender": "female", "age_group": "adult"}]}


class FakeAgent:
    async def simulate_monologue(self, solo, goal, lines=2, kids=False):
        return [{"speaker": "Emma", "text": "Mama!", "expression": "crying", "action": ""}]

    async def simulate_interaction(self, *a, **k):
        return [{"speaker": "Emma", "text": "Mama!", "expression": "crying", "action": ""}]

    async def simulate_dialogue(self, *a, **k):
        return [{"speaker": "Emma", "text": "Mama!", "expression": "crying", "action": ""}]


async def main():
    svc = object.__new__(LLMService); svc.agent_service = FakeAgent()
    calls = []
    answers = [json.dumps(BAD1), json.dumps(BAD2)]
    async def fake(prompt):
        calls.append(prompt)
        return json.dumps(CHARS) if "List these characters" in prompt else answers.pop(0)
    svc._generate = fake
    out = await svc.generate_script_structure(STORY, "Drama", "Warm", keep_story=True)
    names = [c.name for c in out.characters]
    assert names == ["Emma", "Sarah"], names
    assert out.title.startswith("Emma was a small child"), out.title
    assert out.genre == "Drama" and out.tone == "Warm" and out.visual_style, (out.genre, out.tone, out.visual_style)
    assert len(out.scenes) == 1 and out.scenes[0].location.startswith("INT. KITCHEN") and out.scenes[0].visual_description
    print("OK 1: scenes-only answer filled in: title, genre/tone/style, characters", names)

    # the character call itself fails -> simple entries, still no crash
    answers[:] = [json.dumps(BAD1), json.dumps(BAD2)]
    async def fake2(prompt):
        if "List these characters" in prompt:
            raise RuntimeError("model busy")
        return answers.pop(0)
    svc._generate = fake2
    out = await svc.generate_script_structure(STORY, "Drama", "Warm", keep_story=True)
    assert [c.name for c in out.characters] == ["Emma", "Sarah"] and out.characters[0].appearance_prompt
    print("OK 2: still works when the extra character call fails")

    # nothing usable at all -> the story is cut into scenes
    answers[:] = [json.dumps({"foo": 1}), json.dumps({"bar": 2})]
    svc._generate = fake
    out = await svc.generate_script_structure(STORY, "Drama", "Warm", keep_story=True)
    assert len(out.scenes) >= 1 and out.scenes[0].visual_description
    print("OK 3: no scenes at all -> the story itself is cut into scenes")
    print("ALL PASS")

asyncio.run(main())
