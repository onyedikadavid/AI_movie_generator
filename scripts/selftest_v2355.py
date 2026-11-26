import sys, types
try:
    import httpx  # noqa
except ImportError:
    _h = types.ModuleType("httpx"); _h.__getattr__ = lambda n: type(n, (Exception,), {}); sys.modules["httpx"] = _h
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
                    "app.services.dynamic_config": {"resolve_url": lambda *a, **k: "", "KEY_OLLAMA_URL": "k", "KEY_IMAGE_API_URL": "ki"},
                    "app.services.http_retry": {"post_with_retry": None}}.items():
    sys.modules[name] = types.SimpleNamespace(**attrs)
from app.services.llm_service import LLMService
for bad in ["FORMAT","TONE","EMOTION","SOUND CUE","Camera Cue","Narrator","Music"]:
    assert LLMService._is_label(bad), bad
for ok in ["Michael","Mama Ada","Chidi","Sound Man","Tony"]:
    assert not LLMService._is_label(ok), ok
story="FORMAT: short film\nTONE: warm\nEMOTION: grief\nSOUND CUE: rain\nMichael: Hello.\nAda (softly): Hi."
assert LLMService._speaker_names(story,{"scenes":[{"characters_present":["CAMERA CUE","Michael"]}]})==["CAMERA CUE"][:0]+["Michael","Ada"], LLMService._speaker_names(story,{})
# image service must not go local
from app.core.config import settings
import app.services.image_service as im
im.resolve_url=lambda k,f:""
settings.IMAGE_API_URL=""
try:
    im.ImageGenerationService().generate_image("x","/tmp/_x.png"); raise SystemExit("should have raised")
except RuntimeError as e:
    assert "could not be found" in str(e)
# cutout cool-down
import app.services.bible_assets as ba
calls=[]
class S:
    def cutout(self,a,b): calls.append(1); raise ConnectionError("reset")
ba.local_cutout=lambda s,d: open(d,"wb").write(b"x") and True or True
import os,tempfile
d=tempfile.mkdtemp()
ba.character_path=lambda p,i: os.path.join(d,f"{i}.png"); ba.cutout_path=lambda p,i: os.path.join(d,f"{i}_c.png")
for i in (1,2,3): open(ba.character_path("p",i),"wb").write(b"img")
ch=lambda i: types.SimpleNamespace(id=i,name=f"c{i}")
for i in (1,2,3): ba.ensure_cutout(S(),"p",ch(i))
assert len(calls)==1, calls
import app.services.scene_images as si
from PIL import Image
env=os.path.join(d,"env.png"); Image.new("RGB",(300,200),(40,90,40)).save(env)
cutp=os.path.join(d,"p.png"); Image.new("RGBA",(60,120),(200,50,50,255)).save(cutp)
out=os.path.join(d,"o.png")
assert ba.local_compose(env,[(cutp,0.6)],out,300,200) and Image.open(out).size==(300,200)
print("v2.35.5 selftest OK")
for bad in ("VISUAL CUE","FOCUS","Music Cue"):
    assert LLMService._is_label(bad), bad
print("labels ok")
