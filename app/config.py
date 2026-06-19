"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden via env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    use_in_memory_store: bool = Field(default=False, alias="USE_IN_MEMORY_STORE")

    # ---- Infra ----
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/transactions",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # ---- RQ / queue ----
    rq_queue_name: str = Field(default="default", alias="RQ_QUEUE_NAME")
    worker_concurrency: int = Field(default=1, alias="WORKER_CONCURRENCY")

    # ---- LLM (Gemini 1.5 Flash via google-generativeai) ----
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    llm_model: str = Field(default="gemini-1.5-flash", alias="LLM_MODEL")
    llm_batch_size: int = Field(default=20, alias="LLM_BATCH_SIZE")

    # ---- Uploads ----
    upload_dir: str = Field(default="/tmp/uploads", alias="UPLOAD_DIR")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance.

    Wrapped in a function so tests can monkeypatch the env before each call.
    """
    return Settings()
