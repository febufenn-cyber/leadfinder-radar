"""The guardrail block (DESIGN §3.7) — the safety core of api-send."""

import random
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.guardrails import Verdict, check_send, jitter_delay
from app.models.halt import Halt
from app.models.send import Send

SETTINGS = Settings(_env_file=None)  # Asia/Kolkata, quiet 23-7, caps 8/5/3

# 12:00 IST == 06:30 UTC — comfortably outside quiet hours
NOON_IST = datetime(2026, 7, 10, 6, 30, tzinfo=UTC)
# 00:30 IST == 19:00 UTC previous day — inside quiet hours
NIGHT_IST = datetime(2026, 7, 9, 19, 0, tzinfo=UTC)


def make_send(**overrides) -> Send:
    kwargs = dict(
        lead_id=1, draft_id=1, approval_event_id=1,
        platform="reddit", channel="comment",
        target_external_id="t3_x", community="smallbusiness",
        text="hi", scheduled_at=NOON_IST,
    )
    kwargs.update(overrides)
    return Send(**kwargs)


async def sent_row(session, **overrides) -> Send:
    s = make_send(status="sent", sent_at=NOON_IST - timedelta(hours=1), **overrides)
    session.add(s)
    await session.flush()
    return s


async def test_clean_send_allowed(db_session):
    verdict = await check_send(db_session, make_send(), SETTINGS, now=NOON_IST)
    assert verdict == Verdict(True)


async def test_active_halt_blocks_without_retry(db_session):
    db_session.add(Halt(platform="reddit", reason="mod removal on t1_abc"))
    await db_session.flush()
    verdict = await check_send(db_session, make_send(), SETTINGS, now=NOON_IST)
    assert not verdict.allowed
    assert verdict.retry_at is None  # human must clear it
    assert "halted" in verdict.reason


async def test_global_halt_blocks_other_platform(db_session):
    db_session.add(Halt(platform="all", reason="manual pause"))
    await db_session.flush()
    verdict = await check_send(
        db_session, make_send(platform="threads"), SETTINGS, now=NOON_IST
    )
    assert not verdict.allowed


async def test_cleared_halt_does_not_block(db_session):
    db_session.add(Halt(platform="reddit", reason="old", cleared_at=NOON_IST))
    await db_session.flush()
    verdict = await check_send(db_session, make_send(), SETTINGS, now=NOON_IST)
    assert verdict.allowed


async def test_quiet_hours_defer_to_morning(db_session):
    verdict = await check_send(db_session, make_send(), SETTINGS, now=NIGHT_IST)
    assert not verdict.allowed
    assert verdict.reason == "quiet hours"
    # 07:00 IST == 01:30 UTC on the 10th
    assert verdict.retry_at == datetime(2026, 7, 10, 1, 30, tzinfo=UTC)


async def test_reddit_daily_cap(db_session):
    for i in range(SETTINGS.CAP_REDDIT_COMMENTS_PER_DAY):
        await sent_row(db_session, community=f"sub{i}")
    verdict = await check_send(
        db_session, make_send(community="freshsub"), SETTINGS, now=NOON_IST
    )
    assert not verdict.allowed
    assert "daily cap" in verdict.reason
    assert verdict.retry_at is not None  # resumes tomorrow morning


async def test_failed_and_cancelled_do_not_consume_cap(db_session):
    for status in ("failed", "cancelled", "queued"):
        s = make_send(status=status, community="subx")
        s.sent_at = NOON_IST - timedelta(hours=1)
        db_session.add(s)
    await db_session.flush()
    verdict = await check_send(
        db_session, make_send(community="fresh"), SETTINGS, now=NOON_IST
    )
    assert verdict.allowed


async def test_dm_cap_counts_across_platforms(db_session):
    await sent_row(db_session, channel="dm", platform="reddit", community=None, recipient="a")
    await sent_row(db_session, channel="dm", platform="threads", community=None, recipient="b")
    await sent_row(db_session, channel="dm", platform="reddit", community=None, recipient="c")
    verdict = await check_send(
        db_session,
        make_send(channel="dm", community=None, recipient="d"),
        SETTINGS,
        now=NOON_IST,
    )
    assert not verdict.allowed
    assert "3/3" in verdict.reason


async def test_community_cooldown_one_per_day(db_session):
    await sent_row(db_session, community="smallbusiness")
    verdict = await check_send(
        db_session, make_send(community="smallbusiness"), SETTINGS, now=NOON_IST
    )
    assert not verdict.allowed
    assert "cooldown" in verdict.reason
    other = await check_send(
        db_session, make_send(community="startups"), SETTINGS, now=NOON_IST
    )
    assert other.allowed


def test_jitter_bounds():
    rng = random.Random(42)
    for _ in range(50):
        d = jitter_delay(SETTINGS, rng)
        assert timedelta(minutes=2) <= d <= timedelta(minutes=9)
