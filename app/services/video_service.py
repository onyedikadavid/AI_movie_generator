import os
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class VideoGenerationService:
    """
    Handles video clip generation by calling out to an image-to-video model
    server (e.g. the Colab/Kaggle-hosted LTX-Video server in
    /notebooks/colab_video_server.ipynb, a local ComfyUI workflow, or any
    server implementing the same contract).

    IMPORTANT: the image (and optional pose map) are sent as file bytes over
    HTTP, not as local file paths. That's required as soon as the video
    model runs anywhere other than the exact machine running this backend
    (e.g. a free Colab/Kaggle GPU reached over a tunnel) - the remote
    machine can't read your local disk.
    """

    def __init__(self):
        self.comfy_url = getattr(settings, "COMFYUI_URL", "http://localhost:8188")
        self.wan_api_url = settings.WAN_API_URL

    def generate_video_from_image(
        self,
        image_path: str,
        output_path: str,
        motion_prompt: str,
        narration_text: str = "",
        audio_duration: float = 3.5,
        pose_map_path: str = None,
    ) -> str:
        """
        Generates a video clip guided by an image keyframe and a motion prompt.
        Raises RuntimeError if generation fails, so a broken video backend
        surfaces as a clear pipeline failure instead of silently producing a
        scene with a missing video file that FFmpeg then fails on anyway.
        """
        logger.info(f"Requesting video generation for: '{motion_prompt}' (duration: {audio_duration}s)")

        if not os.path.exists(image_path):
            raise RuntimeError(f"Keyframe image not found at {image_path}; cannot generate video.")

        data = {
            "motion_prompt": motion_prompt,
            "text_cue": narration_text or "",
            "duration": str(audio_duration),
        }

        files = {"image": (os.path.basename(image_path), open(image_path, "rb"), "image/png")}
        if pose_map_path and os.path.exists(pose_map_path):
            files["pose_map"] = (os.path.basename(pose_map_path), open(pose_map_path, "rb"), "image/png")

        try:
            with httpx.Client(timeout=600.0) as client:
                response = client.post(self.wan_api_url, data=data, files=files)
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Couldn't reach the video generation server at {self.wan_api_url}. "
                f"If you're using the Colab notebook, check that it's still running and that "
                f"WAN_API_URL in .env matches its current public tunnel URL (these change every "
                f"time you restart the notebook). Original error: {e}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Video generation request failed: {e}") from e
        finally:
            for _, (_, fh, _) in files.items():
                fh.close()

        if response.status_code != 200:
            raise RuntimeError(
                f"Video generation server returned {response.status_code}: {response.text[:300]}"
            )

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(response.content)

        return output_path
