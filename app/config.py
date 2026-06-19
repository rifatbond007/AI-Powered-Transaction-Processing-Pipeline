"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    All values can be overridden via env vars (see .env.example).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Used in Segment 4 — listed here so .env.example is consistent.
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/transactions",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # ETL
    csv_path: str = Field(default="transactions.csv", alias="CSV_PATH")
    summary_cache_ttl_seconds: int = Field(default=60, alias="SUMMARY_CACHE_TTL")


def get_settings() -> Settings:
    """Return a fresh :class:`Settings` instance.

    Wrapped in a function so tests can monkeypatch the env before each call.
    """
    return Settings()
