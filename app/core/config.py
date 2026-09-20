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