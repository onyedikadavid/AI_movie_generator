# AI Story-to-Video Engine — Backend

FastAPI + Celery backend for the multi-character AI video production pipeline.

## What changed in this pass (backend audit)

The codebase was reviewed end-to-end against the two PRDs and the frontend
that now consumes it. Fixes made:

1. **Alembic migration was completely out of sync with the models**
   (wrong column names, and the entire `scripts` table was missing). This is
   why `init_db.py` / `force_create.py` existed as a `create_all()`
   workaround. The migration in `alembic/versions/` has been rewritten to
   match `app/models/*.py` exactly.
2. **Two-phase pipeline.** Previously, creating a project immediately ran
   the *entire* pipeline (script → images → video → composite) with no
   chance to review or edit anything, contradicting the PRD's own flow
   ("Scene & Asset Review" happens *before* "Asset & Video Generation").
   The Celery task is now split:
   - `run_script_breakdown` — STT + LLM parsing only. Runs automatically on
     project creation. Leaves the project in `SCRIPT_READY`.
   - `run_asset_pipeline` — image/video/audio generation + composition.
     Triggered explicitly via `POST /api/v1/projects/{id}/run-pipeline`,
     matching the PRD's documented endpoint. Reads scenes back out of the
     database (not the original LLM output), so edits made during review
     are respected.
   - Added `Scene.dialogue_turns` (JSON column) so multi-character dialogue
     generated in Phase 1 survives into Phase 2.
3. **Missing endpoints the frontend needs** — added `GET /projects` (list,
   for the dashboard), a richer `GET /projects/{id}` (script + characters +
   scenes, for the review/player views), `PATCH .../scenes/{id}` and
   `PATCH .../characters/{id}` (human-in-the-loop editing before
   generation), and `DELETE /projects/{id}`.
4. **`/health` endpoint** was an empty file; implemented a real check of
   DB/Redis/Ollama reachability, which the frontend polls for a status
   indicator.
5. **`PipelineOrchestrator`** referenced `ProjectStatus.IN_PROGRESS` /
   `PENDING`, which don't exist on the model (dead code that would raise
   `AttributeError` the moment it ran) — fixed to use real statuses and the
   two real tasks.
6. **Shell-injection risk in `TTSService`** — it built a shell string with
   raw LLM-generated text inside `subprocess.run(..., shell=True)`. Switched
   to argument-list `subprocess.run` with text piped via stdin.
7. `VideoGenerationService`'s default `WAN_API_URL` pointed at
   `localhost:8000`, the same port the FastAPI app itself listens on.
   Changed the default to `8188` (ComfyUI's default) so a misconfigured env
   doesn't silently loop back into this API.
8. Static file mount (`/storage`) added to `main.py` so the frontend can
   render generated keyframes/videos directly by URL.
9. CORS origins are now configurable via `CORS_ORIGINS` instead of hardcoded
   `*`.
10. `requirements.txt`: added `Pillow` (used directly by `SpatialService`
    but never declared), and clarified that `piper` (TTS) and `controlnet_aux`
    (pose maps) are external/optional installs, not pip packages that
    `pip install -r requirements.txt` alone will fully satisfy.
11. `VideoGenerationService` sent the video backend a **local file path**,
    which only works if the video model runs on the exact same machine as
    this API. Rewrote it to upload the actual image bytes over HTTP instead,
    so a remote GPU (e.g. the Colab notebook in `../notebooks/`) can receive
    the keyframe. Also changed it to raise a clear error (failing the
    project with a real message) instead of silently continuing when the
    video backend is unreachable or errors.

## Local setup

```bash
cp .env.example .env          # adjust if your ports/creds differ
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Postgres + Redis (or run these two via `docker compose up db redis`)
alembic upgrade head

# Terminal 1
uvicorn main:app --reload

# Terminal 2
celery -A app.core.celery_app.celery_app worker --loglevel=info
# On Windows, add --pool=solo: python -m celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo
# Celery's default "prefork" pool relies on Unix fork(), which Windows
# doesn't have - it's officially Celery's own recommendation to use the
# solo pool on Windows instead, and it also sidesteps some flaky
# reconnect-after-idle behavior that shows up more on Windows.

# Terminal 3 - local LLM
ollama serve
ollama pull llama3
```

Then run the frontend (see `../movie-pipeline-frontend/README.md`) against
`http://localhost:8000`.

### Optional local generation backends

- **Images**: `ImageGenerationService` loads SDXL via `diffusers` on first
  use if `IMAGE_API_URL` is unset (needs a GPU with enough VRAM to be
  practical; downloads ~7GB of weights to this machine the first time).
  Set `IMAGE_API_URL` to skip local generation entirely and call a remote
  server instead - `../notebooks/asset_generation_server.ipynb` runs SDXL
  *and* the video model together on one free Colab/Kaggle GPU behind one
  ngrok tunnel, so nothing downloads locally.
- **Video**: `VideoGenerationService` posts the keyframe image (as file bytes,
  not a path - see below) to `WAN_API_URL`. If unreachable or it errors, the
  project fails with a clear message rather than silently producing a scene
  with a missing clip.
  - **No video model of your own?** `../notebooks/ltx_video_server.ipynb`
    runs a free-tier-friendly open video model (LTX-Video) on a free Google
    Colab GPU and exposes it over an ngrok tunnel - paste the URL it prints
    into `WAN_API_URL`. Kaggle notebooks work the same way (enable
    "Internet" in notebook settings, run the same install/serve cells,
    ngrok works there too) if you'd rather use Kaggle's free 30 GPU-hrs/week
    instead of Colab's session limits.
  - **Be realistic about output quality.** This gets you real generated
    motion per shot instead of a still image panned around - not
    theatrical-film quality. That ceiling belongs to the model
    (LTX-Video/Wan/etc.), not this codebase; there's no configuration that
    changes it.
- **TTS**: install the `piper` CLI binary and a voice model
  (see https://github.com/rhasspy/piper) for real narration; otherwise a
  silent placeholder clip is generated so the pipeline keeps moving.

## No Docker (e.g. Windows without virtualization enabled)

Docker Desktop needs hardware virtualization (WSL2 or Hyper-V) - if your
machine can't enable that, skip Docker entirely and use free hosted
services instead of local Postgres/Redis:

- **Postgres**: [Neon](https://neon.tech) (free tier, no card required).
  Copy its connection string into `DATABASE_URL_OVERRIDE` in `.env` - see
  the comment above that field for the exact format asyncpg expects.
- **Redis**: [Upstash](https://upstash.com) (free tier, serverless Redis).
  Copy its `rediss://` connection string into `REDIS_URL_OVERRIDE`.

With both set, `alembic upgrade head`, the Celery worker, and `uvicorn`
all work exactly the same as with local/Dockerized services - nothing else
in the setup changes.

## Deploying to Render (or Railway/Fly/similar)

Three things that are easy to miss and will make the deployed API
completely unreachable or silently broken even though it "deployed
successfully":

1. **`.env` never reaches the server.** It's gitignored (correctly - it can
   hold real secrets), which means if you deploy from a git repo, Render
   never sees it at all. Every variable you want in production -
   `DATABASE_URL_OVERRIDE`, `REDIS_URL_OVERRIDE`, `CORS_ORIGINS`,
   `OLLAMA_URL`, `IMAGE_API_URL`, `WAN_API_URL` - has to be re-entered
   directly in Render's dashboard under the service's **Environment**
   tab, separately from your local `.env`.
2. **`CORS_ORIGINS` must list your actual deployed frontend URL**, not just
   `localhost`. If it doesn't, the browser blocks every response with a
   CORS error that surfaces to the user as a generic "Failed to fetch" -
   indistinguishable, from the frontend's point of view, from the backend
   being down entirely. Set it to something like:
   `CORS_ORIGINS=https://your-frontend.vercel.app`
   (comma-separate multiple origins; no trailing slash on any of them).
3. **The port Render routes traffic to is dynamic**, injected as the
   `$PORT` env var - it is *not* 8000. The `Dockerfile` in this repo reads
   it correctly (`--port ${PORT:-8000}`, falling back to 8000 only when
   `$PORT` isn't set, e.g. local `docker run`). If you ever hardcode a
   port in a Start Command instead of using this Dockerfile as-is, use
   `--port $PORT`, not a fixed number - a hardcoded port is the single
   most common reason a Render web service builds fine but is completely
   unreachable from outside.

You'll also need a **second** Render service (type: Background Worker, not
Web Service) running the Celery worker command below, pointed at the same
repo and given the *same* environment variables as the API service -
otherwise projects get created but never actually process, since nothing
is consuming the queue:
```
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

This repo ships two requirements files:
- `requirements-render.txt` - what the Dockerfile actually installs.
  Lightweight; assumes Ollama/image/video generation happen remotely
  (Colab/Kaggle notebooks) rather than in-process.
- `requirements.txt` - the full list, including `torch`/`diffusers`/
  `faster-whisper`, for running everything locally on your own GPU
  machine instead (no Docker needed there - just `pip install` directly).

## Docker

`docker-compose.yml` brings up Postgres, Redis, the API, and a Celery
worker. The worker's GPU reservation block requires the NVIDIA Container
Toolkit; if you're on a CPU-only machine, delete that `deploy:` block from
the `celery_worker` service before running `docker compose up`.
