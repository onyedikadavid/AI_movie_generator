import logging
import time
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

# Fixed keys the Colab/Kaggle notebooks publish their current ngrok URL
# under. Keep these in sync with the notebooks' own "publish URL" cells.
KEY_OLLAMA_URL = "dynamic:ollama_url"
KEY_IMAGE_API_URL = "dynamic:image_api_url"
KEY_WAN_API_URL = "dynamic:wan_api_url"


_LAST_GOOD: dict = {}   # key -> the last URL Upstash gave us, so one failed lookup does not lose it


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

    last_err = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(
                    f"{settings.UPSTASH_REDIS_REST_URL}/get/{key}",
                    headers={"Authorization": f"Bearer {settings.UPSTASH_REDIS_REST_TOKEN}"},
                )
            if response.status_code == 200:
                value = response.json().get("result")
                if value and _looks_like_url(value):
                    _LAST_GOOD[key] = value
                    return value
                if value:
                    logger.warning(
                        f"Dynamic config value for '{key}' doesn't look like a valid URL "
                        f"({value!r}) - using static fallback instead. This usually means "
                        f"something published a malformed value (e.g. an old notebook run "
                        f"before a bugfix) - re-run the notebook's publish cell to overwrite it."
                    )
                break                      # answered, just no usable value: retrying will not change that
            last_err = f"HTTP {response.status_code}"
        except Exception as e:
            last_err = e
        time.sleep(0.5 * (attempt + 1))
    else:
        if _LAST_GOOD.get(key):
            logger.warning("Dynamic config lookup for '%s' failed (%s) - using the last URL that worked.", key, last_err)
            return _LAST_GOOD[key]
        logger.info(f"Dynamic config lookup for '{key}' failed, using static fallback: {last_err}")

    return fallback


def _looks_like_url(value: str) -> bool:
    """Cheap sanity check so a malformed published value (stray whitespace,
    an accidentally-stringified Python object, etc.) can't silently get
    handed to httpx and fail with a confusing low-level parse error - we'd
    rather fall back to the static URL and log a clear warning instead."""
    value = value.strip()
    return (
        value.startswith(("http://", "https://"))
        and " " not in value
        and '"' not in value
        and "'" not in value
    )
