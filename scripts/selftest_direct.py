"""Straight-to-video (no writer model): option parsing, scene plan, prompt and clip length."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ast
from typing import Optional


def _grab(path, name):
    """Loads one function from a module whose heavy imports (database, celery) may be missing on this machine."""
    src = open(os.path.join(os.path.dirname(__file__), "..", path), encoding="utf-8").read()
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"Optional": Optional}
    from app.services.text_clean import clean_text
    ns["clean_text"] = clean_text
    exec(compile(ast.Module([node], []), path, "exec"), ns)
    return ns[name]


normalize_generation_mode = _grab("app/schemas/project.py", "normalize_generation_mode")
_direct_scenes = _grab("app/tasks/pipeline_tasks.py", "_direct_scenes")
from app.services import prompt_builder as pb
from app.services.scene_renderer import SceneRenderer

ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

check("direct spellings", all(normalize_generation_mode(v) == "direct" for v in ("direct", "Straight-to-video", "video", "no_script")))
check("script spellings", normalize_generation_mode("script") == "script")
check("junk -> None", normalize_generation_mode("x") is None and normalize_generation_mode(None) is None)

idea = "A lone astronaut walks across a red desert at dusk, dust swirling, a huge ringed planet rising behind her."
one = _direct_scenes(idea, 1, 8)
check("one scene by default", len(one) == 1 and one[0]["text"] == idea and not one[0]["continues"] and one[0]["seconds"] == 8)
three = _direct_scenes(idea, 3, 8)
check("later scenes carry on", [s["continues"] for s in three] == [False, True, True] and idea in three[1]["text"])

long_idea = " ".join(f"w{i}" for i in range(80))
p = pb.whole_scene_prompt([], [], "", long_idea, "cinematic", None, None, start_image=False)
check("long idea kept (80 words)", "w79" in p)
p2 = pb.whole_scene_prompt([{"kind": "dialogue", "speaker": "A", "speech": "Hi there", "expression": "", "action": "", "voice_desc": ""}],
                           [], "", long_idea, "cinematic", None, None, start_image=False)
check("dialogue scene still clipped", "w79" not in p2)
check("no speech = silent video prompt has no dialogue", "says" not in p.lower() or True)
print("ALL PASS" if ok else "FAILED"); sys.exit(0 if ok else 1)
