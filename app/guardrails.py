"""The guardrail block (DESIGN §3.7) — enforced in code, not prompts.

Every send is checked at EXECUTION time (not just queue time): active halt,
quiet hours, per-platform daily caps, per-community cooldown. Halts beat
everything and require manual reset. Days are counted in the owner's timezone.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.halt import Halt
from app.models.send import Send


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    retry_at: datetime | None = None  # None + not allowed = blocked until human action


def jitter_delay(settings: Settings, rng: random.Random | None = None) -> timedelta:
    """Mandatory 2-9 min (config) delay after approval — never post instantly."""
    rng = rng or random.Random()
    seconds = rng.randint(settings.JITTER_MIN_MINUTES * 60, settings.JITTER_MAX_MINUTES * 60)
    return timedelta(seconds=seconds)


def _quiet_until(now_utc: datetime, settings: Settings) -> datetime | None:
    """If inside quiet hours, the UTC instant they end; else None."""
    tz = ZoneInfo(settings.OWNER_TZ)
    local = now_utc.astimezone(tz)
    start, end = settings.QUIET_HOURS_START, settings.QUIET_HOURS_END
    if start == end:
        return None
    if start < end:
        in_quiet = start <= local.hour < end
    else:  # wraps midnight, e.g. 23 -> 7
        in_quiet = local.hour >= start or local.hour < end
    if not in_quiet:
        return None
    end_local = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if end_local <= local:
        end_local += timedelta(days=1)
    return end_local.astimezone(UTC)


def _next_morning(now_utc: datetime, settings: Settings) -> datetime:
    """Tomorrow at quiet-hours end, owner-local — where capped sends resume."""
    tz = ZoneInfo(settings.OWNER_TZ)
    local = now_utc.astimezone(tz)
    morning = (local + timedelta(days=1)).replace(
        hour=settings.QUIET_HOURS_END, minute=0, second=0, microsecond=0
    )
    return morning.astimezone(UTC)


def _owner_day_start(now_utc: datetime, settings: Settings) -> datetime:
    tz = ZoneInfo(settings.OWNER_TZ)
    local = now_utc.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _cap_for(send: Send, settings: Settings) -> tuple[int, list]:
    """(cap, extra where-clauses defining the counting scope)."""
    if send.channel == "dm":
        return settings.CAP_DMS_PER_DAY, [Send.channel == "dm"]
    if send.platform == "reddit":
        return settings.CAP_REDDIT_COMMENTS_PER_DAY, [
            Send.platform == "reddit", Send.channel == "comment",
        ]
    return settings.CAP_THREADS_REPLIES_PER_DAY, [
        Send.platform == "threads", Send.channel == "comment",
    ]


async def check_send(
    session: AsyncSession, send: Send, settings: Settings, now: datetime | None = None
) -> Verdict:
    now = now or datetime.now(UTC)

    # 1. halt beats everything; only a human clears it
    halt = (
        await session.execute(
            select(Halt).where(
                Halt.platform.in_([send.platform, "all"]), Halt.cleared_at.is_(None)
            )
        )
    ).scalars().first()
    if halt is not None:
        return Verdict(False, f"halted: {halt.reason} (clear with scripts/clear_halt.py)")

    # 2. quiet hours
    quiet_end = _quiet_until(now, settings)
    if quiet_end is not None:
        return Verdict(False, "quiet hours", retry_at=quiet_end)

    day_start = _owner_day_start(now, settings)

    # 3. per-platform daily cap (only successful sends consume budget)
    cap, scope = _cap_for(send, settings)
    used = await session.scalar(
        select(func.count()).select_from(Send).where(
            Send.status == "sent", Send.sent_at >= day_start, *scope
        )
    )
    if (used or 0) >= cap:
        return Verdict(
            False, f"daily cap reached ({used}/{cap})", retry_at=_next_morning(now, settings)
        )

    # 4. per-community cooldown: max 1/day/sub (DESIGN §3.7)
    if send.community:
        already = await session.scalar(
            select(func.count()).select_from(Send).where(
                Send.status == "sent",
                Send.sent_at >= day_start,
                Send.community == send.community,
            )
        )
        if (already or 0) >= 1:
            return Verdict(
                False,
                f"community cooldown: already sent in {send.community} today",
                retry_at=_next_morning(now, settings),
            )

    return Verdict(True)
