"""arq worker: runs a poll cycle every POLL_INTERVAL_MINUTES (DESIGN §2 scheduler).

Run: uv run arq app.worker.WorkerSettings
"""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.pipeline import run_draft_cycle, run_poll_cycle
from app.sending import run_send_cycle
from app.watch import run_watch_cycle

logging.basicConfig(
    level=get_settings().LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs full request URLs at INFO — the Telegram URL contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def poll_job(ctx: dict) -> dict:
    return await run_poll_cycle()


async def draft_job(ctx: dict) -> dict:
    return await run_draft_cycle()


async def send_job(ctx: dict) -> dict:
    return await run_send_cycle()


async def watch_job(ctx: dict) -> dict:
    return await run_watch_cycle()


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
            unique=True,  # a long cycle delays the next tick instead of overlapping it
            timeout=600,  # classify only (~13s/lead typical); drafting is decoupled
        ),
        cron(
            draft_job,
            second=30,  # every minute, offset from the poll tick
            run_at_startup=True,
            unique=True,
            timeout=1800,  # sonnet drafting runs ~3 min per lead, batch of 3
        ),
        cron(
            send_job,
            second=15,  # every minute — cheap no-op when nothing is due
            run_at_startup=False,  # let a restart settle before posting anything
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
    ]
