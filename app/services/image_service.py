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
        references: list = None,
        reference_scale: float = None,
        init_image: str = None,
        init_strength: float = None,
    ) -> str:
        """`prompt` is used exactly as given - build it with prompt_builder.

        `references`: picture files of the people who must look the same as in the bible (sent to the SDXL server, which
        uses them through IP-Adapter). `init_image` + `init_strength`: the place's own picture, redrawn with the people in
        it. A server without those features simply ignores them and draws from the text."""
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

            files = []
            for path in (references or [])[: max(1, int(settings.BIBLE_REF_MAX))]:
                if path and os.path.exists(path):
                    files.append(("reference_images", (os.path.basename(path), open(path, "rb").read(), "image/png")))
            if files:
                data["reference_scale"] = str(float(settings.BIBLE_REF_SCALE if reference_scale is None else reference_scale))
            if init_image and os.path.exists(init_image):
                files.append(("init_image", (os.path.basename(init_image), open(init_image, "rb").read(), "image/png")))
                data["init_strength"] = str(float(settings.BIBLE_PLACE_STRENGTH if init_strength is None else init_strength))
            response = None
            for attempt in (1, 2, 3):
                try:
                    with httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0), headers=GPU_HEADERS) as client:
                        response = client.post(image_api_url, data=data, files=files or None)
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

    # ------------------------------------------------------------------ cut-outs and composing (SDXL notebook v3)
    @staticmethod
    def _route(route: str):
        url = resolve_url(KEY_IMAGE_API_URL, settings.IMAGE_API_URL)
        if not url:
            return None
        return url.rsplit("/v1/", 1)[0] + route if "/v1/" in url else url.rstrip("/") + route

    def _post_png(self, route: str, data: dict, files: list, output_path: str, what: str) -> bool:
        """True = the PNG was saved. False = this image server is an older notebook that does not have the route."""
        url = self._route(route)
        if not url:
            return False
        last = None
        for attempt in (1, 2, 3):
            try:
                with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0), headers=GPU_HEADERS) as client:
                    response = client.post(url, data=data, files=files)
            except (httpx.ConnectError, *TRANSIENT) as e:
                last = e
                time.sleep(3 * attempt)
                continue
            if response.status_code in (404, 405):
                return False
            if response.status_code in TRANSIENT_STATUS and attempt < 3:
                time.sleep(4 * attempt)
                continue
            if response.status_code != 200:
                raise RuntimeError(explain(response, what))
            if not response.content.startswith(b"\x89PNG"):
                raise RuntimeError(explain(response, what + " (did not return a PNG)"))
            atomic_write(output_path, response.content)
            return True
        raise RuntimeError(f"{what}: the image server could not be reached ({last!r}).")

    def cutout(self, source_path: str, output_path: str) -> bool:
        """The person in `source_path` without the background (transparent PNG), made by the image notebook."""
        with open(source_path, "rb") as fh:
            files = [("image", (os.path.basename(source_path), fh.read(), "image/png"))]
        return self._post_png("/v1/cutout", {}, files, output_path, "Cutting the character out")

    def compose_scene(self, background: str, people: list, output_path: str, prompt: str = "", negative: str = "",
                      refine: float = 0.0, seed: int = None, width: int = None, height: int = None) -> bool:
        """The empty place with the bible characters (`people` = [(cut-out png, height as a fraction of the picture)]) standing
        in it, left to right, then a light redraw so they take the light of the place."""
        files = [("background", (os.path.basename(background), open(background, "rb").read(), "image/png"))]
        heights = []
        for path, frac in people:
            files.append(("people", (os.path.basename(path), open(path, "rb").read(), "image/png")))
            heights.append(f"{float(frac):.3f}")
        data = {"prompt": prompt, "negative_prompt": negative, "refine_strength": f"{float(refine):.3f}",
                "heights": ",".join(heights), "width": str(int(width or settings.IMAGE_WIDTH)),
                "height": str(int(height or settings.IMAGE_HEIGHT))}
        if seed is not None:
            data["seed"] = str(int(seed))
        return self._post_png("/v1/compose-scene", data, files, output_path, "Placing the characters in the scene")
