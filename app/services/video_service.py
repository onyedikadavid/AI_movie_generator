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

    def _post(self, data: dict, files=None) -> bytes:
        """POST to the video server with retries; returns the MP4 bytes."""
        # Resolved fresh on every call - picks up a restarted notebook's new URL.
        url = resolve_url(KEY_WAN_API_URL, settings.WAN_API_URL)
        response = None
        for attempt in (1, 2):
            try:
                with httpx.Client(timeout=httpx.Timeout(float(settings.VIDEO_SERVER_TIMEOUT), connect=30.0), headers=GPU_HEADERS) as client:
                    response = client.post(url, data=data, files=files) if files else client.post(url, data=data)
            except httpx.ConnectError as e:
                raise RuntimeError(
                    f"Couldn't reach the video generation server at {url}. If you're using the "
                    f"Colab/Kaggle notebook, check that it's still running and that it published its "
                    f"current URL (these change every time the notebook restarts). Original error: {e}"
                ) from e
            except TRANSIENT as e:
                if attempt == 1:
                    time.sleep(5)
                    continue
                raise RuntimeError(f"Video generation request failed: {e!r}") from e
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"Video generation request failed: {e}") from e
            if response.status_code in TRANSIENT_STATUS and attempt == 1:
                time.sleep(8)
                continue
            break

        if response.status_code != 200:
            raise RuntimeError(explain(response, "Video generation"))
        if len(response.content) < 2000:
            raise RuntimeError(explain(response, "Video generation (returned an empty clip)"))
        return response.content

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
        atomic_write(output_path, self._post(data, files))
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
