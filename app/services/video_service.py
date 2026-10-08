import hashlib
import json
import os
import time
import logging
import httpx
from app.core.config import settings
from app.services.dynamic_config import resolve_url, KEY_WAN_API_URL
from app.services.media_client import GPU_HEADERS, TRANSIENT, TRANSIENT_STATUS, atomic_write, explain

logger = logging.getLogger(__name__)


class VideoGenerationService:
    """
    Image-to-video through the LTX-Video notebook server (or any server that
    implements the same contract - see notebooks/ltx_video_server).

    The image is sent as file BYTES over HTTP (not a path): the GPU lives on
    another machine and can't read this one's disk.
    """

    # ------------------------------------------------------------------ transport
    def _send(self, method: str, url: str, *, data=None, files=None, timeout=None, attempts: int = 3):
        """One HTTP call with retries for network blips. Safe to retry for async jobs (the server recognises
        a repeated job_key and returns the same job instead of starting another)."""
        last = None
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=timeout, headers=GPU_HEADERS) as client:
                    if method == "post":
                        resp = client.post(url, data=data, files=files) if files else client.post(url, data=data)
                    else:
                        resp = client.get(url)
            except httpx.ConnectError as e:
                last = RuntimeError(
                    f"Couldn't reach the video generation server at {url}. If you're using the "
                    f"Colab/Kaggle notebook, check that it's still running and that it published its "
                    f"current URL (these change every time the notebook restarts). Original error: {e}"
                )
            except Exception as e:  # noqa: BLE001 - timeouts, resets, dropped tunnel...
                last = RuntimeError(f"Video server request failed: {e!r}")
            else:
                if resp.status_code in TRANSIENT_STATUS and attempt < attempts:
                    time.sleep(5 * attempt)
                    continue
                return resp
            if attempt < attempts:
                time.sleep(5 * attempt)
        raise last

    def _job_key(self, data: dict, image_bytes: bytes = None) -> str:
        """Same request = same key, so a retry (or a Resume after a pause) is recognised by the server."""
        h = hashlib.sha1(json.dumps(sorted(data.items()), default=str).encode())
        if image_bytes:
            h.update(hashlib.sha1(image_bytes).digest())
        return h.hexdigest()

    def _post(self, data: dict, files=None, image_bytes: bytes = None) -> bytes:
        """Ask the video server for one clip; returns the MP4 bytes."""
        # Resolved fresh on every call - picks up a restarted notebook's new URL.
        url = resolve_url(KEY_WAN_API_URL, settings.WAN_API_URL)

        if not settings.VIDEO_ASYNC:
            resp = self._send("post", url, data=data, files=files,
                              timeout=httpx.Timeout(float(settings.VIDEO_SERVER_TIMEOUT), connect=30.0), attempts=2)
            return self._video_bytes(resp)

        data = {**data, "async": "1", "job_key": self._job_key(data, image_bytes)}
        resp = self._send("post", url, data=data, files=files, timeout=httpx.Timeout(120.0, connect=30.0), attempts=4)
        ctype = (resp.headers.get("content-type") or "").lower()
        if resp.status_code == 200 and "json" not in ctype:
            return self._video_bytes(resp)          # an older server answered with the video directly
        if resp.status_code not in (200, 202) or "json" not in ctype:
            raise RuntimeError(explain(resp, "Video generation"))
        job_id = (resp.json() or {}).get("job_id")
        if not job_id:
            raise RuntimeError(explain(resp, "Video generation (no job id returned)"))
        return self._wait_for_job(url.rsplit("/", 1)[0], job_id)

    def _video_bytes(self, resp) -> bytes:
        if resp.status_code != 200:
            raise RuntimeError(explain(resp, "Video generation"))
        if len(resp.content) < 2000:
            raise RuntimeError(explain(resp, "Video generation (returned an empty clip)"))
        return resp.content

    def _wait_for_job(self, base: str, job_id: str) -> bytes:
        """Poll the job until it is done, then download it. Network blips while polling are tolerated:
        the render keeps going on the GPU server regardless of this connection."""
        jobs_url = f"{base}/jobs/{job_id}"
        deadline = time.time() + float(settings.VIDEO_JOB_MAX_WAIT)
        misses, started, last_log = 0, time.time(), 0.0
        while True:
            if time.time() > deadline:
                raise RuntimeError(
                    f"The video server took longer than {int(settings.VIDEO_JOB_MAX_WAIT / 60)} minutes for one clip "
                    f"(job {job_id}). Check the notebook; Resume will pick the same job up if it is still running."
                )
            time.sleep(max(0, settings.VIDEO_POLL_SECONDS))
            try:
                r = self._send("get", jobs_url, timeout=httpx.Timeout(60.0, connect=20.0), attempts=1)
            except RuntimeError as e:
                misses += 1
                if misses % 5 == 1:
                    logger.warning("Video job %s: can't reach the server right now (%s...) - still waiting.", job_id, str(e)[:90])
                if misses >= 40:
                    raise RuntimeError(
                        f"Lost contact with the video server while job {job_id} was running ({e}). The render may still "
                        "be going: press Resume once the notebook is reachable again and it will pick the same job up."
                    ) from e
                continue
            if r.status_code in TRANSIENT_STATUS:
                misses += 1
                if misses >= 40:
                    raise RuntimeError(explain(r, "Video job status"))
                continue
            misses = 0
            if r.status_code == 404:
                raise RuntimeError(
                    "The video server no longer knows this job - the notebook was restarted while it was rendering. "
                    "Press Resume to start it again."
                )
            if r.status_code != 200:
                raise RuntimeError(explain(r, "Video job status"))
            info = r.json() or {}
            status = info.get("status")
            if status == "done":
                res = self._send("get", f"{jobs_url}/result", timeout=httpx.Timeout(300.0, connect=30.0), attempts=4)
                return self._video_bytes(res)
            if status == "error":
                trace = (info.get("trace") or "")[-700:].replace("\n", " / ")
                raise RuntimeError(f"The video server failed this clip: {info.get('error')}" + (f" | {trace}" if trace else ""))
            if time.time() - last_log > 60:
                last_log = time.time()
                logger.info("Video job %s: %s (%ds so far%s)", job_id, status, int(time.time() - started),
                            f", #{info['queue_position']} in the server's queue" if info.get("queue_position") else "")

    def generate_clip(
        self,
        image_path: str,
        output_path: str,
        prompt: str,
        duration: float,
        seed: int = None,
    ) -> str:
        """Image-to-video: render one short clip (<= ~5 s) that starts from `image_path`."""
        if not os.path.exists(image_path):
            raise RuntimeError(f"Keyframe image not found at {image_path}; cannot generate video.")
        data = {
            "motion_prompt": prompt,
            "text_cue": "",
            "duration": f"{max(1.0, float(duration)):.2f}",
            "fps": str(settings.VIDEO_FPS),
        }
        if seed is not None:
            data["seed"] = str(int(seed))
        # The image is sent as file BYTES (the GPU is on another machine and can't read this disk).
        with open(image_path, "rb") as fh:
            image_bytes = fh.read()
        files = {"image": (os.path.basename(image_path), image_bytes, "image/png")}
        atomic_write(output_path, self._post(data, files, image_bytes))
        return output_path

    def generate_text_clip(self, prompt: str, output_path: str, duration: float, seed: int = None) -> str:
        """Text-to-video: render one short clip from the prompt alone (no start image)."""
        try:
            width, height = (int(v) for v in settings.VIDEO_T2V_RESOLUTION.lower().split("x"))
        except Exception:  # noqa: BLE001
            width, height = 960, 544
        data = {
            "mode": "t2v",
            # Ask the server to include the model's own background sound (the pipeline mixes it with the voices).
            "with_audio": "1" if (settings.AUDIO_MODE or "").lower() == "hybrid" else "",
            "motion_prompt": prompt,
            "duration": f"{max(1.0, float(duration)):.2f}",
            "fps": str(settings.VIDEO_FPS),
            "width": str(width),
            "height": str(height),
        }
        if seed is not None:
            data["seed"] = str(int(seed))
        atomic_write(output_path, self._post(data))
        return output_path

    # Old signature, kept so scripts that still call it keep working.
    def generate_video_from_image(self, image_path, output_path, motion_prompt, narration_text="",
                                  audio_duration=3.5, pose_map_path=None):
        return self.generate_clip(image_path, output_path, motion_prompt, audio_duration)
