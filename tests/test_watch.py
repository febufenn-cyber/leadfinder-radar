"""Watch cycle (DESIGN §3.8/§3.7): reply detection is the only code path past
`sent`; a removed comment trips a platform-wide auto-halt."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.approval import queue_send
from app.core.config import Settings
from app.models.event import Event
from app.models.halt import Halt
from app.models.lead import Lead, transition
from app.models.send import Send
from app.watch import run_watch_cycle
from tests.test_send_cycle import FakeNotifier
from tests.test_send_queue import make_drafted_lead


class FakeReddit:
    def __init__(self, inbox=None, things=None):
        self._inbox = inbox or []
        self._things = things or []

    async def fetch_inbox(self, client, limit=25):
        return self._inbox

    async def fetch_things(self, client, fullnames):
        return [t for t in self._things if t.get("name") in fullnames]


def wire(monkeypatch, fake_reddit, threads_replies=None, threads_token=""):
    monkeypatch.setattr(
        "app.watch.get_settings",
        lambda: Settings(_env_file=None, THREADS_ACCESS_TOKEN=threads_token),
    )
    monkeypatch.setattr("app.senders.reddit_user.get_reddit_user_client", lambda: fake_reddit)

    async def fake_fetch_replies(client, token, media_id):
        return threads_replies or []

    monkeypatch.setattr("app.senders.threads_send.fetch_replies", fake_fetch_replies)


async def make_sent_send(session, platform="reddit", result_id="t1_ours", **overrides):
    lead = await make_drafted_lead(session, **overrides)
    if platform == "threads":
        send = Send(
            lead_id=lead.id,
            draft_id=lead.chosen_draft_id or 1,
            approval_event_id=1,
            platform="threads",
            channel="comment",
            target_external_id="17700000",
            community=None,
            text="hi",
            scheduled_at=datetime.now(UTC),
        )
        session.add(send)
    else:
        send = await queue_send(session, lead.id, "A")
    send.status = "sent"
    send.sent_at = datetime.now(UTC) - timedelta(hours=2)
    send.external_result_id = result_id
    transition(lead, "sent")
    await session.commit()
    return send, lead


async def test_reddit_reply_advances_lead(db_factory, monkeypatch):
    notifier = FakeNotifier()
    hubspot_calls = []

    async def spy_hubspot(session, lead, post, author, preview):
        hubspot_calls.append((lead.id, author))
        return True

    async with db_factory() as session:
        send, lead = await make_sent_send(session)
        lead_id = lead.id

    wire(
        monkeypatch,
        FakeReddit(
            inbox=[{"parent_id": "t1_ours", "author": "shopowner42", "body": "yes please!"}]
        ),
    )
    monkeypatch.setattr("app.watch.hubspot_sync_reply", spy_hubspot)

    summary = await run_watch_cycle(session_factory=db_factory, notifier=notifier)
    assert summary["replies"] == 1

    async with db_factory() as session:
        assert (await session.get(Lead, lead_id)).status == "replied"
        kinds = (await session.execute(select(Event.kind))).scalars().all()
        assert "reply_detected" in kinds
    assert any("🎉" in m and "u/shopowner42" in m for m in notifier.messages)
    assert hubspot_calls == [(lead_id, "u/shopowner42")]


async def test_unrelated_inbox_items_do_nothing(db_factory, monkeypatch):
    async with db_factory() as session:
        _, lead = await make_sent_send(session)
        lead_id = lead.id

    wire(monkeypatch, FakeReddit(inbox=[{"parent_id": "t1_other", "author": "x", "body": "?"}]))
    summary = await run_watch_cycle(session_factory=db_factory, notifier=FakeNotifier())
    assert summary["replies"] == 0
    async with db_factory() as session:
        assert (await session.get(Lead, lead_id)).status == "sent"


async def test_removed_comment_trips_auto_halt_once(db_factory, monkeypatch):
    notifier = FakeNotifier()
    async with db_factory() as session:
        await make_sent_send(session)

    wire(
        monkeypatch,
        FakeReddit(things=[{"name": "t1_ours", "banned_by": "sub_mod", "body": "gone"}]),
    )
    summary = await run_watch_cycle(session_factory=db_factory, notifier=notifier)
    assert summary["halts"] == 1
    assert any("AUTO-HALT" in m for m in notifier.messages)

    # a second cycle must not stack another halt row
    await run_watch_cycle(session_factory=db_factory, notifier=notifier)
    async with db_factory() as session:
        halts = (
            (await session.execute(select(Halt).where(Halt.cleared_at.is_(None)))).scalars().all()
        )
        assert len(halts) == 1
        assert halts[0].platform == "reddit"


async def test_threads_reply_advances_lead(db_factory, monkeypatch):
    notifier = FakeNotifier()

    async def no_hubspot(session, lead, post, author, preview):
        return False

    async with db_factory() as session:
        _, lead = await make_sent_send(
            session,
            platform="threads",
            result_id="17700000123",
            source="threads",
            external_id="th_1",
            community=None,
            url="https://www.threads.net/@maker/post/xyz",
        )
        lead_id = lead.id

    wire(
        monkeypatch,
        FakeReddit(),
        threads_replies=[{"username": "maker", "text": "tell me more"}],
        threads_token="tok",
    )
    monkeypatch.setattr("app.watch.hubspot_sync_reply", no_hubspot)

    summary = await run_watch_cycle(session_factory=db_factory, notifier=notifier)
    assert summary["replies"] == 1
    async with db_factory() as session:
        assert (await session.get(Lead, lead_id)).status == "replied"
    assert any("@maker" in m for m in notifier.messages)


async def test_already_replied_lead_is_not_watched(db_factory, monkeypatch):
    async with db_factory() as session:
        _, lead = await make_sent_send(session)
        transition(lead, "replied")
        await session.commit()

    fake = FakeReddit(inbox=[{"parent_id": "t1_ours", "author": "a", "body": "b"}])
    wire(monkeypatch, fake)
    summary = await run_watch_cycle(session_factory=db_factory, notifier=FakeNotifier())
    assert summary["watched"] == 0
    assert summary["replies"] == 0
