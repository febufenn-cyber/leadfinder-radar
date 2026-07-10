"""Application settings — loaded from environment variables on startup.

Same pattern as Thesis Studio's app/core/config.py.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration. All values come from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---- Application ----
    ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # ---- Storage ----
    DATABASE_URL: str = "postgresql+asyncpg://leadfinder:leadfinder@localhost:5442/leadfinder"
    REDIS_URL: str = "redis://localhost:6380/0"

    # ---- Alerts (empty token -> console fallback) ----
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ---- Polling ----
    REDDIT_USER_AGENT: str = "leadfinder/0.1 (personal keyword monitor)"
    POLL_INTERVAL_MINUTES: int = 2
    PACKS_DIR: str = "packs"

    # ---- Reddit OAuth (empty -> RSS fallback adapter) ----
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""

    # ---- Claude (CLI subprocess on Max OAuth; DESIGN §4 auth note) ----
    CLAUDE_CLI_PATH: str = "claude"
    CLAUDE_FAST_MODEL: str = "claude-haiku-4-5-20251001"
    CLAUDE_STANDARD_MODEL: str = "claude-sonnet-4-6"
    CLASSIFY_TIMEOUT_SECONDS: int = 90
    DRAFT_TIMEOUT_SECONDS: int = 240  # sonnet writing 2-3 variants is slower than haiku


@lru_cache
def get_settings() -> Settings:
    return Settings()
