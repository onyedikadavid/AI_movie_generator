import sys, types, json, os, tempfile
sys.path.insert(0, os.getcwd())
# --- stubs: pydantic_settings + a fake httpx driven by a fake GPU server ---
m=types.ModuleType("pydantic_settings")
class B: pass
m.BaseSettings=B; m.SettingsConfigDict=lambda **k:k; sys.modules["pydantic_settings"]=m

class _E(Exception): pass
class ReadError(_E): pass
class ConnectError(_E): pass
class RemoteProtocolError(_E): pass
class WriteError(_E): pass
class ConnectTimeout(_E): pass
class Timeout:
    def __init__(self,*a,**k): pass
class Response:
    def __init__(self,status,body=None,ctype="application/json",raw=None):
        self.status_code=status; self.headers={"content-type":ctype}
        self._body=body; self.content=raw if raw is not None else json.dumps(body or {}).encode()
        self.text=self.content.decode(errors="replace") if raw is None else "<binary>"
    def json(self): return self._body

SERVER=None
class Client:
    def __init__(self,*a,**k): pass
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def post(self,url,data=None,files=None): return SERVER.post(url,data or {},files)
    def get(self,url): return SERVER.get(url)

h=types.ModuleType("httpx")
for n,o in dict(ReadError=ReadError,ConnectError=ConnectError,RemoteProtocolError=RemoteProtocolError,WriteError=WriteError,
                ConnectTimeout=ConnectTimeout,Timeout=Timeout,Response=Response,Client=Client).items(): setattr(h,n,o)
sys.modules["httpx"]=h

from app.core.config import settings
import app.services.video_service as vs
vs.time.sleep=lambda s: None            # no real waiting in the test
settings.VIDEO_POLL_SECONDS=0

class FakeServer:
    """Mimics the Kaggle job server."""
    def __init__(self, **kw):
        self.jobs={}; self.keys={}; self.created=0; self.kw=kw; self.post_calls=0; self.get_calls=0
    def post(self,url,data,files):
        self.post_calls+=1
        if self.kw.get("sync_only"):
            return Response(200,raw=b"MP4"*1000,ctype="video/mp4")
        if self.kw.get("drop_first_post_after_accept") and self.post_calls==1:
            self._accept(data); raise ReadError("connection dropped after the server accepted the job")
        jid,dup=self._accept(data)
        return Response(202,{"job_id":jid,"status":"queued","duplicate":dup})
    def _accept(self,data):
        key=data["job_key"]
        if key in self.keys: return self.keys[key],True
        self.created+=1; jid=f"job{self.created}"; self.keys[key]=jid; self.jobs[jid]={"polls":0}; return jid,False
    def get(self,url):
        self.get_calls+=1
        if self.kw.get("dead"): raise ConnectError("tunnel is gone")
        jid=url.split("/jobs/")[1].split("/")[0]
        if self.kw.get("restarted"): return Response(404,{"error":"unknown job"})
        job=self.jobs[jid]
        if url.endswith("/result"): return Response(200,raw=b"VIDEO"*2000,ctype="video/mp4")
        job["polls"]+=1
        if self.kw.get("blips") and job["polls"] in (2,3): raise ReadError("blip while polling")
        if self.kw.get("fail"): return Response(200,{"status":"error","error":"RuntimeError: CUDA out of memory","trace":"File x\nline y"})
        if job["polls"]<5: return Response(200,{"status":"running","queue_position":None})
        return Response(200,{"status":"done"})

svc=vs.VideoGenerationService()
def run(**kw):
    global SERVER
    SERVER=FakeServer(**kw); out=tempfile.mktemp(suffix=".mp4")
    return SERVER, svc.generate_text_clip("a prompt", out, 3.5, 42), out

# 1. happy path, with a dropped connection right after the server accepted the job, plus blips while polling
srv,path,out=run(drop_first_post_after_accept=True, blips=True)
assert os.path.getsize(out)==10000
assert srv.created==1, f"the retry must NOT start a second job (created {srv.created})"
print("OK 1: connection dropped after the job was accepted + polling blips -> clip delivered; GPU jobs created:", srv.created)

# 2. an older server that answers with the video directly still works
srv,path,out=run(sync_only=True); assert os.path.getsize(out)==3000
print("OK 2: old-style server (video in the response) still works")

# 3. the server reports a failed render
try: run(fail=True); raise SystemExit("should have failed")
except RuntimeError as e: assert "CUDA out of memory" in str(e); print("OK 3: server-side failure is reported with its reason ->", str(e)[:70])

# 4. notebook restarted: job unknown
try: run(restarted=True); raise SystemExit("should have failed")
except RuntimeError as e: assert "restarted" in str(e) and "Resume" in str(e); print("OK 4: restarted notebook -> clear message ->", str(e)[:70])

# 5. tunnel gone for good
try: run(dead=True); raise SystemExit("should have failed")
except RuntimeError as e: assert "Lost contact" in str(e) and "Resume" in str(e); print("OK 5: lost contact -> clear message after", SERVER.get_calls, "polls")

# 6. job key: same request = same key; different prompt/seed/picture = different key
k=lambda d,img=None: svc._job_key(d,img)
d={"motion_prompt":"p","duration":"3.5","seed":"1"}
assert k(d)==k(dict(d)) and k(d)!=k({**d,"seed":"2"}) and k(d,b"imgA")!=k(d,b"imgB") and k(d,b"imgA")==k(d,b"imgA")
print("OK 6: job keys are stable for identical requests and differ when anything changes")
print("ALL VIDEO CLIENT CHECKS PASSED")
