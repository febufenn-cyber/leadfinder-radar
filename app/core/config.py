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

    # ---- Threads official API (empty token -> adapter disabled) ----
    THREADS_ACCESS_TOKEN: str = ""
    THREADS_DAILY_QUERY_BUDGET: int = 48
    THREADS_MIN_INTERVAL_MINUTES: int = 15

    # ---- Threads discovery via Google CSE (either empty -> adapter disabled).
    # Compliant bridge while threads_keyword_search public access sits behind
    # Meta App Review; leads are copy-mode only. 60-min spacing keeps a
    # 4-query pack inside the free 100-queries/day tier. ----
    GOOGLE_CSE_KEY: str = ""
    GOOGLE_CSE_ID: str = ""
    GOOGLE_CSE_MIN_INTERVAL_MINUTES: int = 60

    # ---- API-send guardrails (M4; DESIGN §3.7 — enforced in code, not prompts) ----
    SEND_MODE: Literal["copy", "api"] = "copy"
    OWNER_TZ: str = "Asia/Kolkata"
    QUIET_HOURS_START: int = 23  # owner-local
    QUIET_HOURS_END: int = 7
    CAP_REDDIT_COMMENTS_PER_DAY: int = 8
    CAP_THREADS_REPLIES_PER_DAY: int = 5
    CAP_DMS_PER_DAY: int = 3  # across platforms
    JITTER_MIN_MINUTES: int = 2
    JITTER_MAX_MINUTES: int = 9
    WATCH_INTERVAL_MINUTES: int = 5

    # ---- Reddit user auth (script-app password grant; needed for api-send + watch) ----
    REDDIT_USERNAME: str = ""
    REDDIT_PASSWORD: str = ""

    # ---- HubSpot (empty -> sync disabled) ----
    HUBSPOT_ACCESS_TOKEN: str = ""

    # ---- Claude (CLI subprocess on Max OAuth; DESIGN §4 auth note) ----
    CLAUDE_CLI_PATH: str = "claude"
    CLAUDE_FAST_MODEL: str = "claude-haiku-4-5-20251001"
    CLAUDE_STANDARD_MODEL: str = "claude-sonnet-4-6"
    CLASSIFY_TIMEOUT_SECONDS: int = 90
    DRAFT_TIMEOUT_SECONDS: int = 240  # sonnet writing 2-3 variants is slower than haiku


@lru_cache
def get_settings() -> Settings:
    return Settings()
