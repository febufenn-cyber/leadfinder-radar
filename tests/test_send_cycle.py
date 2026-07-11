"""Send cycle (DESIGN §3.7): due sends execute, guardrails re-check at
execution time, failures never retry silently."""

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.approval import queue_send
from app.core.config import Settings
from app.models.event import Event
from app.models.halt import Halt
from app.models.lead import Lead
from app.models.send import Send
from app.sending import run_send_cycle
from tests.test_send_queue import make_drafted_lead

# 12:00 IST — outside quiet hours (defaults: 23-7 Asia/Kolkata)
NOON_IST = datetime(2026, 7, 10, 6, 30, tzinfo=UTC)
# 00:30 IST — inside quiet hours
NIGHT_IST = datetime(2026, 7, 9, 19, 0, tzinfo=UTC)


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, text: str) -> bool:
        self.messages.append(text)
        return True


def fresh_settings(monkeypatch):
    """Pin defaults — the developer's real .env must not steer these tests."""
    monkeypatch.setattr("app.sending.get_settings", lambda: Settings(_env_file=None))


async def make_due_send(session, variant="A") -> Send:
    lead = await make_drafted_lead(session)
    send = await queue_send(session, lead.id, variant, rng=random.Random(1))
    send.scheduled_at = NOON_IST - timedelta(minutes=1)
    await session.commit()
    return send


async def ok_exec(send, client):
    return True, "t1_posted", None


async def fail_exec(send, client):
    return False, None, "RATELIMIT: try again"


async def test_due_send_executes_and_advances_lead(db_factory, monkeypatch):
    fresh_settings(monkeypatch)
    notifier = FakeNotifier()
    async with db_factory() as session:
        send = await make_due_send(session)
        send_id, lead_id = send.id, send.lead_id

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=ok_exec, now=NOON_IST
    )
    assert summary["executed"] == 1

    async with db_factory() as session:
        send = await session.get(Send, send_id)
        assert send.status == "sent"
        assert send.external_result_id == "t1_posted"
        assert send.sent_at is not None
        lead = await session.get(Lead, lead_id)
        assert lead.status == "sent"
        kinds = (await session.execute(select(Event.kind))).scalars().all()
        assert "send_executed" in kinds
    assert any("✅" in m for m in notifier.messages)


async def test_not_yet_due_send_is_left_alone(db_factory, monkeypatch):
    fresh_settings(monkeypatch)
    async with db_factory() as session:
        send = await make_due_send(session)
        send.scheduled_at = NOON_IST + timedelta(minutes=5)  # future
        await session.commit()
        send_id = send.id

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=FakeNotifier(), execute_fn=ok_exec, now=NOON_IST
    )
    assert summary == {"executed": 0, "deferred": 0, "halted": 0, "failed": 0}
    async with db_factory() as session:
        assert (await session.get(Send, send_id)).status == "queued"


async def test_quiet_hours_defer_send_not_drop_it(db_factory, monkeypatch):
    fresh_settings(monkeypatch)
    async with db_factory() as session:
        send = await make_due_send(session)
        send.scheduled_at = NIGHT_IST - timedelta(minutes=1)
        await session.commit()
        send_id = send.id

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=FakeNotifier(), execute_fn=ok_exec, now=NIGHT_IST
    )
    assert summary["deferred"] == 1
    async with db_factory() as session:
        send = await session.get(Send, send_id)
        assert send.status == "queued"  # still approved, just later
        assert send.scheduled_at == datetime(2026, 7, 10, 1, 30, tzinfo=UTC)  # 07:00 IST


async def test_active_halt_blocks_and_notifies(db_factory, monkeypatch):
    fresh_settings(monkeypatch)
    notifier = FakeNotifier()
    async with db_factory() as session:
        send = await make_due_send(session)
        session.add(Halt(platform="reddit", reason="mod warning"))
        await session.commit()
        send_id = send.id

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=ok_exec, now=NOON_IST
    )
    assert summary["halted"] == 1
    async with db_factory() as session:
        assert (await session.get(Send, send_id)).status == "halted"
    assert any("🛑" in m for m in notifier.messages)


async def test_failed_send_records_error_and_never_retries(db_factory, monkeypatch):
    fresh_settings(monkeypatch)
    notifier = FakeNotifier()
    async with db_factory() as session:
        send = await make_due_send(session)
        send_id, lead_id = send.id, send.lead_id

    await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=fail_exec, now=NOON_IST
    )
    async with db_factory() as session:
        send = await session.get(Send, send_id)
        assert send.status == "failed"
        assert "RATELIMIT" in send.error
        assert (await session.get(Lead, lead_id)).status == "drafted"  # re-approvable

    # second cycle must NOT touch the failed send (no silent retry)
    summary = await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=ok_exec, now=NOON_IST
    )
    assert summary["executed"] == 0
    assert any("re-approve" in m for m in notifier.messages)


async def test_cancelled_while_iterating_is_skipped(db_factory, monkeypatch):
    fresh_settings(monkeypatch)
    async with db_factory() as session:
        send = await make_due_send(session)
        send.status = "cancelled"
        await session.commit()

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=FakeNotifier(), execute_fn=ok_exec, now=NOON_IST
    )
    assert summary == {"executed": 0, "deferred": 0, "halted": 0, "failed": 0}


async def test_orphaned_executing_send_fails_never_reposts(db_factory, monkeypatch):
    """The double-post guard: a send stuck in 'executing' (crash between the API
    call and its commit) must be failed with a check-the-thread warning — and
    NEVER handed to the executor again."""
    fresh_settings(monkeypatch)
    notifier = FakeNotifier()
    executed = []

    async def spying_exec(send, client):
        executed.append(send.id)
        return True, "t1_dup", None

    async with db_factory() as session:
        send = await make_due_send(session)
        send.status = "executing"  # simulate the crash window
        await session.commit()
        send_id = send.id

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=spying_exec, now=NOON_IST
    )
    assert executed == []  # the executor never saw it
    assert summary["failed"] == 1
    async with db_factory() as session:
        send = await session.get(Send, send_id)
        assert send.status == "failed"
        assert "MAY already be live" in send.error
        kinds = (await session.execute(select(Event.kind))).scalars().all()
        assert "send_orphaned" in kinds
    assert any("interrupted mid-post" in m for m in notifier.messages)


async def test_cancel_loses_race_once_claimed(db_factory, monkeypatch):
    """cancel_send is an atomic UPDATE WHERE status='queued' — once the cycle
    has claimed the row ('executing'), cancel must refuse."""
    fresh_settings(monkeypatch)
    from app.approval import cancel_send

    async with db_factory() as session:
        send = await make_due_send(session)
        send.status = "executing"
        await session.commit()
        send_id = send.id
        assert await cancel_send(session, send_id) is False

    async with db_factory() as session:
        send2 = await session.get(Send, send_id)
        assert send2.status == "executing"  # untouched by the failed cancel


async def test_executor_exception_marks_failed_and_continues(db_factory, monkeypatch):
    """An executor crash mid-HTTP has an UNKNOWN outcome: mark failed with the
    check-the-thread warning, keep the loop alive for other sends."""
    fresh_settings(monkeypatch)
    notifier = FakeNotifier()

    async def exploding_exec(send, client):
        raise RuntimeError("connection reset mid-request")

    async with db_factory() as session:
        send = await make_due_send(session)
        send_id = send.id

    summary = await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=exploding_exec, now=NOON_IST
    )
    assert summary["failed"] == 1
    async with db_factory() as session:
        send = await session.get(Send, send_id)
        assert send.status == "failed"
        assert "MAY be live" in send.error

    # and it must not be retried next cycle
    summary = await run_send_cycle(
        session_factory=db_factory, notifier=notifier, execute_fn=ok_exec, now=NOON_IST
    )
    assert summary["executed"] == 0
