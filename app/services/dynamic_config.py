import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

# Fixed keys the Colab/Kaggle notebooks publish their current ngrok URL
# under. Keep these in sync with the notebooks' own "publish URL" cells.
KEY_OLLAMA_URL = "dynamic:ollama_url"
KEY_IMAGE_API_URL = "dynamic:image_api_url"
KEY_WAN_API_URL = "dynamic:wan_api_url"


def resolve_url(key: str, fallback: str) -> str:
    """
    Looks up the current value of `key` in Upstash's REST API (a plain
    GET, no redis client library needed) and returns it if present;
    otherwise returns `fallback` (the static *_URL from .env).

    This exists so restarting a free Colab/Kaggle notebook - which issues
    a brand new ngrok URL every time - doesn't require hand-editing this
    backend's .env (locally) AND Render's dashboard (in production) every
    single time. The notebook publishes its new URL once; every service
    call here picks it up automatically on the next request.

    Deliberately fails soft: if Upstash's REST API isn't configured, is
    unreachable, or the key doesn't exist yet, this returns `fallback`
    silently rather than raising - a flaky lookup for a "nice to have"
    convenience should never be why script/asset generation fails.
    """
    if not settings.UPSTASH_REDIS_REST_URL or not settings.UPSTASH_REDIS_REST_TOKEN:
        return fallback

    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(
                f"{settings.UPSTASH_REDIS_REST_URL}/get/{key}",
                headers={"Authorization": f"Bearer {settings.UPSTASH_REDIS_REST_TOKEN}"},
            )
        if response.status_code == 200:
            value = response.json().get("result")
            if value:
                return value
    except Exception as e:
        logger.info(f"Dynamic config lookup for '{key}' failed, using static fallback: {e}")

    return fallback
