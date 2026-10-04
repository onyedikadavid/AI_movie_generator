import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.schema_upgrade import ensure_schema
from app.core.sync_db import engine as sync_engine
from app.api.v1.router import api_router

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bring the database schema up to date automatically (idempotent, never raises).
    await asyncio.to_thread(ensure_schema, sync_engine)
    yield


app = FastAPI(title=settings.PROJECT_NAME, version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

# Serve generated keyframes/videos/audio so the frontend can render them directly,
# e.g. http://localhost:8000/storage/projects/<id>/scene_1/keyframe.png
os.makedirs(settings.STORAGE_DIR, exist_ok=True)
app.mount("/storage", StaticFiles(directory=settings.STORAGE_DIR), name="storage")

if __name__ == "__main__":
    # Local dev only. On Render/any host that assigns a dynamic port via
    # $PORT, set the service's Start Command to:
    #   uvicorn main:app --host 0.0.0.0 --port $PORT
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
