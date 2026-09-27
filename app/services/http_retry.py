import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

# Transient, connection-level failures - a dropped/reset connection, not a
# real error response. Seen in practice over the free ngrok tunnel + free
# Colab GPU setup under repeated rapid calls (e.g. AgentService's multi-turn
# dialogue simulation making several calls in quick succession). Retrying a
# couple of times is far cheaper than failing an entire multi-step task
# (discarding every successful call already made) over one blip.
_RETRYABLE_EXCEPTIONS = (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.WriteError)


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
    **kwargs,
) -> httpx.Response:
    """
    POSTs with a few retries on transient connection-level failures only.
    Does NOT retry on an actual HTTP error response (4xx/5xx) - those are
    real errors from the server (bad request, out of memory, etc.) that a
    retry won't fix; the caller is expected to check response.status_code
    itself, same as before this helper existed.
    """
    last_exc: Exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await client.post(url, **kwargs)
        except _RETRYABLE_EXCEPTIONS as e:
            last_exc = e
            if attempt < max_attempts:
                wait = backoff_seconds * attempt
                logger.warning(
                    f"Transient error calling {url} (attempt {attempt}/{max_attempts}): {e!r} - retrying in {wait:.0f}s"
                )
                await asyncio.sleep(wait)
    raise last_exc
