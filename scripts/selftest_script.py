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
BIBLE = {"logline": "A fisherman races a storm to save his village", "main_character": "Tunde wants to warn the village",
         "problem": "the storm and his broken boat", "turning_point": "the boat engine dies", "ending": "the village is saved",
         "message": "courage", "hook": "a giant wave towering over the village", "story": "A giant wave towers over the village. Tunde runs to his boat. The engine dies. He rows. The village is saved."}
GOOD = {"title": "Storm Runner", "genre": "drama", "tone": "tense", "visual_style": "cinematic",
        "characters": [{"name": "Tunde", "description": "fisherman", "appearance_prompt": "40yo man", "gender": "male", "age_group": "adult"}],
        "scenes": [{"scene_number": i + 1, "story_beat": b, "location": "coast", "visual_description": "x", "narration_text": "A wave rises.",
                    "characters_present": ["Tunde"], "image_prompt": "i", "motion_prompt": "m", "duration_seconds": 8}
                   for i, b in enumerate(["hook", "setup", "complication", "turning point", "ending"])]}
WEAK = {**GOOD, "scenes": GOOD["scenes"][:1]}

class FakeAgent:
    goals = []
    async def simulate_monologue(self, solo, goal, lines=2, kids=False):
        FakeAgent.goals.append(goal); return [{"speaker": "Tunde", "text": "We must go now.", "expression": "tense", "action": ""}]

async def main():
    svc = object.__new__(LLMService); svc.agent_service = FakeAgent()
    answers = [json.dumps(BIBLE), json.dumps(WEAK), json.dumps(GOOD)]
    async def fake(prompt):
        calls.append(prompt); return answers.pop(0)
    svc._generate = fake
    out = await svc.generate_script_structure("A fisherman must warn his village about a storm", "Drama", "Tense")
    assert len(calls) == 3, len(calls)
    assert "STORY BIBLE" in calls[1] and "giant wave" in calls[1] and "HOOK" in calls[1] and "beginning, middle and end" in calls[1]
    assert "PROBLEMS" in calls[2] and "fewer than 3 scenes" in calls[2]
    assert len(out.scenes) == 5 and out.scenes[0].story_beat == "hook"
    assert out.story.startswith("A giant wave") and out.hook == "a giant wave towering over the village" and out.story_outline["ending"] == "the village is saved"
    assert "THE FULL STORY" in calls[1] and "The village is saved." in calls[1] and "\"story\"" in calls[0]
    assert any("hook" in g or "ending" in g for g in FakeAgent.goals), FakeAgent.goals
    print("OK story analysis -> scenes with hook/ending rules; weak script got one fix-up pass; beat reaches the dialogue writer")
async def main2():
    # an explicit scene count is enforced; no count -> automatic range in the prompt
    svc = object.__new__(LLMService); svc.agent_service = FakeAgent(); calls.clear()
    three = {**GOOD, "scenes": GOOD["scenes"][:3]}
    answers = [json.dumps(BIBLE), json.dumps(GOOD)]            # model returns 5 although 3 were asked
    async def fake(prompt):
        calls.append(prompt); return answers.pop(0) if answers else json.dumps(three)
    svc._generate = fake
    out = await svc.generate_script_structure("A fisherman must warn his village about a storm", scene_count=3, scene_seconds=8)
    assert "EXACTLY 3 scenes" in calls[1] and "about 8 seconds" in calls[1]
    assert len(out.scenes) == 3 and [s.scene_number for s in out.scenes] == [1, 2, 3]
    calls.clear(); answers[:] = [json.dumps(BIBLE), json.dumps(GOOD)]
    await svc.generate_script_structure("A fisherman must warn his village about a storm")
    assert "Choose the number of scenes the story needs" in calls[1] and "no more, no fewer" not in calls[1]
    print("OK explicit scene count enforced (extra scenes dropped, renumbered); no count = automatic")

asyncio.run(main()); asyncio.run(main2())
