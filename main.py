import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.api.v1.router import api_router

app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0")

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
    # rather than relying on this block - that's what actually reads the
    # host-assigned port; this hardcoded fallback is just for `python main.py`
    # on your own machine.
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)