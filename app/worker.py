"""arq worker: polling, drafting, guarded sends, reply-watch, and M5 review nudges."""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.pipeline import run_draft_cycle, run_poll_cycle
from app.review import run_weekly_review_nudge
from app.sending import run_send_cycle
from app.watch import run_watch_cycle

logging.basicConfig(
    level=get_settings().LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def poll_job(ctx: dict) -> dict:
    return await run_poll_cycle()


async def draft_job(ctx: dict) -> dict:
    return await run_draft_cycle()


async def send_job(ctx: dict) -> dict:
    return await run_send_cycle()


async def watch_job(ctx: dict) -> dict:
    return await run_watch_cycle()


async def review_nudge_job(ctx: dict) -> dict:
    return await run_weekly_review_nudge()


_settings = get_settings()

if 60 % _settings.POLL_INTERVAL_MINUTES:
    raise ValueError(
        f"POLL_INTERVAL_MINUTES={_settings.POLL_INTERVAL_MINUTES} must divide 60 evenly "
        "or the cron minute-set produces uneven gaps"
    )


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.REDIS_URL)
    cron_jobs = [
        cron(
            poll_job,
            minute=set(range(0, 60, _settings.POLL_INTERVAL_MINUTES)),
            run_at_startup=True,
            unique=True,
            timeout=600,
        ),
        cron(
            draft_job,
            second=30,
            run_at_startup=True,
            unique=True,
            timeout=1800,
        ),
        cron(
            send_job,
            second=15,
            run_at_startup=False,
            unique=True,
            timeout=120,
        ),
        cron(
            watch_job,
            minute=set(range(0, 60, _settings.WATCH_INTERVAL_MINUTES)),
            second=45,
            run_at_startup=False,
            unique=True,
            timeout=300,
        ),
        # Monday 09:00 IST = 03:30 UTC. The event-ledger guard still prevents
        # duplicate nudges if a deployment or manual run repeats this job.
        cron(
            review_nudge_job,
            weekday=0,
            hour=3,
            minute=30,
            run_at_startup=False,
            unique=True,
            timeout=60,
        ),
    ]
