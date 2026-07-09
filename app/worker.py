"""arq worker: runs a poll cycle every POLL_INTERVAL_MINUTES (DESIGN §2 scheduler).

Run: uv run arq app.worker.WorkerSettings
"""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.pipeline import run_poll_cycle

logging.basicConfig(
    level=get_settings().LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def poll_job(ctx: dict) -> dict:
    return await run_poll_cycle()


_settings = get_settings()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.REDIS_URL)
    cron_jobs = [
        cron(
            poll_job,
            minute=set(range(0, 60, _settings.POLL_INTERVAL_MINUTES)),
            run_at_startup=True,
            unique=True,
            timeout=300,
        )
    ]
