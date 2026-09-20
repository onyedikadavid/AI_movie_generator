# Lightweight image for deploying to a CPU-only host (Render, Railway, etc.)
# now that Ollama/image/video generation run remotely (see the Colab/Kaggle
# notebooks + IMAGE_API_URL/WAN_API_URL/OLLAMA_URL). This image only needs
# to run FastAPI + Celery and call out over HTTP - no CUDA, no torch.
#
# Running everything locally on your own GPU box instead, with real local
# SDXL/Whisper/video generation? Don't use this Dockerfile - run directly
# with `pip install -r requirements.txt` (the full list) on a machine with
# an actual GPU and drivers installed; a container adds no value there.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# `piper` (local TTS) is deliberately NOT installed here - its release asset
# naming changes between versions and a wrong URL would break this build.
# Without it, TTSService automatically falls back to gTTS (if reachable) or
# a silent placeholder clip (see app/services/tts_service.py) - the
# pipeline still runs, just without real narration audio. Add piper
# yourself here (check https://github.com/rhasspy/piper/releases for the
# current asset name) if you want real local narration in this container.

WORKDIR /app

COPY requirements-render.txt .
RUN pip install --no-cache-dir -r requirements-render.txt

COPY . .

EXPOSE 8000

# IMPORTANT: Render (and most PaaS hosts) assign a dynamic port via the
# $PORT env var and route external traffic to *that* port, not 8000. The
# shell form (not the exec/array form) is required here so $PORT actually
# gets expanded - CMD ["uvicorn", ..., "--port", "$PORT"] would pass the
# literal string "$PORT" to uvicorn and fail. ${PORT:-8000} falls back to
# 8000 for local `docker run`/`docker compose` where $PORT isn't set.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
