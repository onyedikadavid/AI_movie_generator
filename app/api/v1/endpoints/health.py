import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.celery_app import celery_app
from app.services.dynamic_config import resolve_url, KEY_OLLAMA_URL, KEY_IMAGE_API_URL, KEY_WAN_API_URL

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Aggregate readiness check for the services the pipeline depends on.
    The frontend polls this to show a live "backend status" indicator.
    """
    status = {
        "api": "ok",
        "database": "unknown",
        "redis": "unknown",
        "ollama": "unknown",
    }

    # Database
    try:
        await db.execute(text("SELECT 1"))
        status["database"] = "ok"
    except Exception as e:
        status["database"] = f"error: {e}"

    # Redis / Celery broker
    try:
        conn = celery_app.connection()
        conn.ensure_connection(max_retries=1, timeout=2)
        conn.release()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    # Ollama (local LLM) - check the live dynamic URL if one's been
    # published by the Colab/Kaggle notebook, so this reflects what
    # generation will actually use rather than a stale static value.
    ollama_url = resolve_url(KEY_OLLAMA_URL, settings.OLLAMA_URL)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            status["ollama"] = "ok" if resp.status_code == 200 else f"error: HTTP {resp.status_code}"
    except Exception as e:
        status["ollama"] = f"error: {e}"

    # Core services decide overall health. The GPU notebooks are informational:
    # only one of them is normally running at a time, so "offline" is not an error.
    overall_ok = all(v == "ok" for v in status.values())

    async def probe(key: str, fallback: str) -> str:
        url = resolve_url(key, fallback)
        if not url:
            return "not configured"
        base = url.split("/v1/")[0].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=3.0, headers={"ngrok-skip-browser-warning": "true"}) as client:
                resp = await client.get(f"{base}/health")
            return "ok" if resp.status_code == 200 else "offline"
        except Exception:
            return "offline"

    status["image_server"] = await probe(KEY_IMAGE_API_URL, settings.IMAGE_API_URL)
    status["video_server"] = await probe(KEY_WAN_API_URL, settings.WAN_API_URL)
    return {"status": "ok" if overall_ok else "degraded", "services": status}
