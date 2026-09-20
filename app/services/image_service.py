import os
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class ImageGenerationService:
    """
    Generates scene keyframe images from a text prompt.

    Two modes, chosen automatically by whether IMAGE_API_URL is set:
      - Remote (IMAGE_API_URL set): POSTs the prompt to a server implementing
        the same contract as ../notebooks/asset_generation_server.ipynb (an
        SDXL server you can run for free on Colab/Kaggle) and saves back
        whatever image bytes it returns. Nothing downloads to this machine.
      - Local (IMAGE_API_URL unset): loads SDXL directly via `diffusers`,
        the original behavior. Downloads ~7GB of weights to this machine
        the first time it runs, and needs a real GPU to be practical.
    """

    def __init__(self):
        self.image_api_url = settings.IMAGE_API_URL
        self.pipeline = None  # only loaded lazily in local mode

    def _load_local_pipeline(self):
        if self.pipeline is None:
            import torch
            from diffusers import AutoPipelineForText2Image

            self.pipeline = AutoPipelineForText2Image.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0",
                torch_dtype=torch.float16,
                variant="fp16",
                use_safetensors=True,
            )
            if torch.cuda.is_available():
                self.pipeline.to("cuda")
                try:
                    self.pipeline.enable_xformers_memory_efficient_attention()
                except Exception as e:
                    logger.info(f"xformers not available, continuing without it: {e}")

    def generate_image(self, prompt: str, output_path: str, negative_prompt: str = "") -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        enhanced_prompt = f"{prompt}, highly detailed, cinematic lighting, photorealistic, 8k resolution"
        default_negative = "blurry, low quality, distorted features, extra limbs, bad anatomy"

        if self.image_api_url:
            data = {
                "prompt": enhanced_prompt,
                "negative_prompt": negative_prompt or default_negative,
            }
            try:
                with httpx.Client(timeout=300.0) as client:
                    response = client.post(self.image_api_url, data=data)
            except httpx.ConnectError as e:
                raise RuntimeError(
                    f"Couldn't reach the image generation server at {self.image_api_url}. "
                    f"Check that the Colab/Kaggle notebook is still running and that "
                    f"IMAGE_API_URL in .env matches its current tunnel URL. Original error: {e}"
                ) from e

            if response.status_code != 200:
                raise RuntimeError(
                    f"Image generation server returned {response.status_code}: {response.text[:300]}"
                )

            with open(output_path, "wb") as f:
                f.write(response.content)
            return output_path

        # Local fallback
        self._load_local_pipeline()
        image = self.pipeline(
            prompt=enhanced_prompt,
            negative_prompt=negative_prompt or default_negative,
            num_inference_steps=30,
            guidance_scale=7.5,
        ).images[0]
        image.save(output_path)
        return output_path
