import os
from urllib.parse import quote_plus
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Story-to-Video Engine"
    API_V1_STR: str = "/api/v1"
    
    # Database & Queues
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    POSTGRES_SERVER: str = os.getenv("POSTGRES_SERVER", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "story_video_db")
    
    # If a hosted Postgres (e.g. Neon, Supabase) gives you a full connection
    # string directly, set DATABASE_URL_OVERRIDE in .env and skip the
    # POSTGRES_* fields above entirely. Needs "postgresql+asyncpg://" as the
    # scheme (not plain "postgresql://") and, for most hosted providers,
    # "?ssl=require" on the end since they require TLS.
    DATABASE_URL_OVERRIDE: str = os.getenv("DATABASE_URL_OVERRIDE", "")

    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        # Safely encodes special characters in password
        encoded_password = quote_plus(self.POSTGRES_PASSWORD)
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{encoded_password}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: str = os.getenv("REDIS_PORT", "6379")

    # If using a hosted Redis (e.g. Upstash, Redis Cloud), set REDIS_URL_OVERRIDE
    # to the full connection string they give you (starts with "redis://" or,
    # for TLS - which Upstash/Redis Cloud require - "rediss://") and skip
    # REDIS_HOST/REDIS_PORT above entirely.
    REDIS_URL_OVERRIDE: str = os.getenv("REDIS_URL_OVERRIDE", "")

    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_URL_OVERRIDE:
            return self.REDIS_URL_OVERRIDE
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # LLM & External Endpoints
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    LLM_MODEL: str = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3:latest"))
    AGENT_MODEL: str = os.getenv("AGENT_MODEL", os.getenv("OLLAMA_MODEL", "llama3:latest"))

    # Optional generation backends (safe no-op fallbacks if unreachable)
    # Point IMAGE_API_URL and WAN_API_URL at the same Colab/Kaggle notebook
    # (see ../notebooks/asset_generation_server.ipynb) to keep SDXL and the
    # video model on one free GPU behind one ngrok tunnel, instead of
    # downloading SDXL's ~7GB of weights to this machine.
    # Leave IMAGE_API_URL empty to fall back to loading SDXL locally (the
    # original behavior) - useful if you do have a local GPU and don't mind
    # the download.
    IMAGE_API_URL: str = os.getenv("IMAGE_API_URL", "")
    WAN_API_URL: str = os.getenv("WAN_API_URL", "http://localhost:8188/v1/generate")

    # Optional: instead of hand-editing OLLAMA_URL/IMAGE_API_URL/WAN_API_URL
    # every time a Colab/Kaggle notebook restarts and gets a new ngrok URL,
    # the notebooks can publish their current URL to a tiny key in your
    # Upstash Redis's REST API, and this backend reads the live value from
    # there instead - see app/services/dynamic_config.py. Get these two
    # values from your Upstash console's "REST API" tab (NOT the same as
    # the rediss:// connection string used for Celery). Leave both blank to
    # disable this entirely and use only the static *_URL values above,
    # exactly as before.
    UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
    UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

    # Optional: S3-compatible object storage (e.g. Cloudflare R2, which has a
    # free tier) for generated keyframes and the final video. Needed whenever
    # the Celery worker and the API run on DIFFERENT machines (e.g. worker on
    # your PC, API on Render): the worker writes files to its own disk, but the
    # browser fetches media from the API - which doesn't have those files.
    # Leave these blank to keep files on local disk only (fine if the API and
    # worker share a filesystem, e.g. both running on your own machine).
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "")
    S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "")
    S3_REGION: str = os.getenv("S3_REGION", "auto")
    # Public base URL files are served from, e.g. https://pub-xxxx.r2.dev
    S3_PUBLIC_BASE_URL: str = os.getenv("S3_PUBLIC_BASE_URL", "")

    # Alternative to S3-compatible storage: Cloudinary (also has a free tier,
    # no card required). Uses a different upload API, not S3, so these are
    # separate settings - if both are set, Cloudinary takes priority.
    CLOUDINARY_CLOUD_NAME: str = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY: str = os.getenv("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET: str = os.getenv("CLOUDINARY_API_SECRET", "")

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------
    # The worker refreshes a heartbeat every HEARTBEAT_SECONDS while it is
    # really running. An "active" project whose heartbeat is older than
    # STALE_RUN_SECONDS is treated as a dead run (worker crashed / was killed)
    # instead of being shown as "Generating..." forever.
    HEARTBEAT_SECONDS: int = 5
    STALE_RUN_SECONDS: int = 90

    # ------------------------------------------------------------------
    # Voices (text-to-speech)
    # ------------------------------------------------------------------
    # "edge" = Microsoft neural voices via the free `edge-tts` package (needs
    # outbound internet, no API key, no cost) - the natural-sounding option.
    # Falls back to Piper (if configured), then gTTS, then silence.
    TTS_ENGINE: str = "edge"
    # auto = pick Nigerian-accented voices when the story is Nigerian, else US/UK.
    # Or force one of: ng | us | gb
    TTS_REGION: str = "auto"
    # Optional override of the narrator's voice (any edge-tts voice short name).
    TTS_NARRATOR_VOICE: str = ""
    # By default the run FAILS (and can be resumed) if the neural voice engine
    # can't be reached, rather than quietly producing a video in the old robotic
    # voice. Set to true to allow falling back to Piper/gTTS/silence instead.
    TTS_ALLOW_FALLBACK: bool = False
    # Optional local Piper voice models (.onnx paths) used only as a fallback.
    PIPER_MODEL_MALE: str = ""
    PIPER_MODEL_FEMALE: str = ""

    # ------------------------------------------------------------------
    # Picture / video quality
    # ------------------------------------------------------------------
    # SDXL keyframe size (landscape, an SDXL-native bucket - never square, so
    # the video model doesn't have to squash it).
    IMAGE_WIDTH: int = 1216
    IMAGE_HEIGHT: int = 832
    # Each scene is cut into shots no longer than this many seconds (the video
    # model is much sharper on short clips than on stretched-out long ones).
    VIDEO_SHOT_SECONDS: float = 4.5
    VIDEO_MAX_SHOTS_PER_SCENE: int = 5
    # Longest clip the video server can produce in one call (121 frames @ 24fps).
    VIDEO_MAX_CLIP_SECONDS: float = 5.0
    VIDEO_FPS: int = 24
    VIDEO_SERVER_TIMEOUT: int = 900
    # Generate a dedicated close-up keyframe for each speaking character
    # (bigger, sharper faces -> far less "melting"). Turn off to render every
    # shot from the scene's wide keyframe instead (faster, lower quality).
    SHOT_COVERAGE: bool = True
    # Final export size and sharpening (0 disables sharpening).
    OUTPUT_WIDTH: int = 1280
    OUTPUT_HEIGHT: int = 720
    OUTPUT_SHARPEN: float = 0.5

    STORAGE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../storage"))

    # Frontend origin(s) allowed to call this API, comma-separated. "*" allows any origin (dev only).
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"  # ✅ Prevents ValidationError when unexpected env vars exist
    )

settings = Settings()
os.makedirs(settings.STORAGE_DIR, exist_ok=True)