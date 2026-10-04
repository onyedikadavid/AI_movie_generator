import os
import time
import logging
import httpx
from app.core.config import settings
from app.services.dynamic_config import resolve_url, KEY_IMAGE_API_URL
from app.services.media_client import GPU_HEADERS, TRANSIENT, TRANSIENT_STATUS, atomic_write, explain

logger = logging.getLogger(__name__)


class ImageGenerationService:
    """
    Generates keyframe images from a text prompt.

    Two modes, chosen automatically by whether IMAGE_API_URL is set (directly,
    or published by the notebook through Upstash):
      - Remote: POSTs the prompt to the SDXL notebook server and saves back
        the PNG it returns. Nothing downloads to this machine.
      - Local: loads SDXL directly via `diffusers` (needs a real GPU).
    """

    def __init__(self):
        self.pipeline = None  # only loaded lazily in local mode

    def _load_local_pipeline(self):
        if self.pipeline is None:
            try:
                import torch
                from diffusers import AutoPipelineForText2Image
            except ImportError as e:
                raise RuntimeError(
                    "No image generation backend is configured, and local SDXL isn't "
                    "installed in this environment. Set IMAGE_API_URL, or publish "
                    "one via the Colab/Kaggle notebook + Upstash dynamic config, so image "
                    "generation has somewhere to actually run."
                ) from e

            self.pipeline = AutoPipelineForText2Image.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0",
                torch_dtype=torch.float16,
                variant="fp16",
                use_safetensors=True,
            )
            if torch.cuda.is_available():
                self.pipeline.to("cuda")

    def generate_image(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: str = "",
        width: int = None,
        height: int = None,
        seed: int = None,
    ) -> str:
        """`prompt` is used exactly as given - build it with prompt_builder."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        width = int(width or settings.IMAGE_WIDTH)
        height = int(height or settings.IMAGE_HEIGHT)
        negative = negative_prompt or "blurry, low quality, distorted face, extra limbs, bad anatomy"

        # Resolved fresh on every call so a notebook restart is picked up on
        # the very next image without restarting this backend.
        image_api_url = resolve_url(KEY_IMAGE_API_URL, settings.IMAGE_API_URL)

        if image_api_url:
            data = {
                "prompt": prompt,
                "negative_prompt": negative,
                "width": str(width),
                "height": str(height),
                "steps": "35",
                "guidance_scale": "6.5",
            }
            if seed is not None:
                data["seed"] = str(int(seed))

            response = None
            for attempt in (1, 2, 3):
                try:
                    with httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0), headers=GPU_HEADERS) as client:
                        response = client.post(image_api_url, data=data)
                except httpx.ConnectError as e:
                    if attempt < 3:
                        time.sleep(3 * attempt)
                        continue
                    raise RuntimeError(
                        f"Couldn't reach the image generation server at {image_api_url}. "
                        f"Check that the SDXL notebook is still running (and that it published its "
                        f"URL / IMAGE_API_URL matches). Original error: {e}"
                    ) from e
                except TRANSIENT as e:
                    if attempt < 3:
                        time.sleep(3 * attempt)
                        continue
                    raise RuntimeError(f"Image request failed repeatedly: {e!r}") from e
                if response.status_code in TRANSIENT_STATUS and attempt < 3:
                    time.sleep(4 * attempt)
                    continue
                break

            if response.status_code != 200:
                raise RuntimeError(explain(response, "Image generation"))
            if len(response.content) < 2000 or not response.content.startswith(b"\x89PNG"):
                raise RuntimeError(explain(response, "Image generation (did not return a PNG)"))
            atomic_write(output_path, response.content)
            return output_path

        # Local fallback
        self._load_local_pipeline()
        import torch

        generator = None
        if seed is not None:
            generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))
        image = self.pipeline(
            prompt=prompt, negative_prompt=negative, width=width, height=height,
            num_inference_steps=35, guidance_scale=6.5, generator=generator,
        ).images[0]
        tmp = f"{output_path}.part.png"
        image.save(tmp)
        os.replace(tmp, output_path)
        return output_path
