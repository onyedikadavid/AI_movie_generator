"""Shared helpers for talking to the remote GPU servers (SDXL / LTX) over ngrok."""
import os

import httpx

# ngrok's free tier shows an HTML "visit site" warning page to anything that
# looks like a browser. This header tells it we're a program.
GPU_HEADERS = {
    "ngrok-skip-browser-warning": "true",
    "bypass-tunnel-reminder": "true",
    "User-Agent": "movie-pipeline-worker/2.0",
}

TRANSIENT = (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.WriteError, httpx.ConnectTimeout)
TRANSIENT_STATUS = {502, 503, 504, 524}


def atomic_write(path: str, data: bytes) -> None:
    """Write fully, then rename - so a crash can never leave a half-written
    file that a later 'resume' would mistake for a finished one."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def explain(resp: httpx.Response, what: str) -> str:
    """Turn a failed GPU-server response into a message that says what went wrong."""
    detail = (resp.text or "")[:300].replace("\n", " ")
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            detail = str(data["error"])
            if data.get("trace"):
                detail += " | server traceback (end): " + str(data["trace"])[-900:].replace("\n", " / ")
    except Exception:  # noqa: BLE001 - body wasn't JSON; keep the raw text
        pass
    hint = ""
    low = detail.lower()
    if "ngrok" in low:
        hint = " (the ngrok tunnel is offline - the notebook has probably stopped or restarted)"
    elif "out of memory" in low:
        hint = " (the GPU ran out of memory - restart the notebook runtime, then press Resume)"
    return f"{what} server returned HTTP {resp.status_code}{hint}: {detail}"