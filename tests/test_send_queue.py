"""SEND_MODE=api approval path (DESIGN §3.7): queue_send / cancel_send.

Same DoD invariant as copy mode — the approval Event is written before the
sends row exists, and the sends row is the ONLY thing the send cycle executes.
"""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.approval import ApprovalError, cancel_send, queue_send
from app.db.session import insert_new_posts
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead, transition
from tests.conftest import make_post_row


async def make_drafted_lead(session, source: str = "reddit", **post_overrides) -> Lead:
    row = make_post_row(source=source, **post_overrides)
    (post,) = await insert_new_posts(session, [row])
    lead = Lead(raw_post_id=post.id, pack="robofox_web")
    session.add(lead)
    await session.flush()
    transition(lead, "drafted")
    session.add(Draft(lead_id=lead.id, variant="A", channel="comment", text="draft A", risk_flags=[]))
    session.add(Draft(lead_id=lead.id, variant="B", channel="dm", text="draft B", risk_flags=[]))
    session.add(
        Draft(lead_id=lead.id, variant="C", channel="comment+dm", text="draft C", risk_flags=[])
    )
    await session.commit()
    return lead


async def test_queue_send_writes_approval_event_first(db_session):
    lead = await make_drafted_lead(db_session)
    send = await queue_send(db_session, lead.id, "A", rng=random.Random(1))
    event = (
        await db_session.execute(select(Event).where(Event.kind == "approval"))
    ).scalars().one()
    assert send.approval_event_id == event.id  # DoD in table form
    assert event.payload["mode"] == "api"
    assert send.platform == "reddit"
    assert send.channel == "comment"
    assert send.target_external_id == "t3_test01"
    assert send.community == "smallbusiness"
    assert send.status == "queued"
    # lead stays drafted until the post actually succeeds
    assert lead.status == "drafted"
    assert lead.chosen_draft_id is not None


async def test_queue_send_schedules_inside_jitter_window(db_session):
    lead = await make_drafted_lead(db_session)
    before = datetime.now(UTC)
    send = await queue_send(db_session, lead.id, "A", rng=random.Random(7))
    after = datetime.now(UTC)
    assert before + timedelta(minutes=2) <= send.scheduled_at <= after + timedelta(minutes=9)


async def test_queue_send_dm_uses_stripped_author_handle(db_session):
    lead = await make_drafted_lead(db_session)
    send = await queue_send(db_session, lead.id, "B", rng=random.Random(1))
    assert send.channel == "dm"
    assert send.recipient == "shopowner42"  # "/u/" prefix stripped


async def test_queue_send_rejects_combo_variant(db_session):
    lead = await make_drafted_lead(db_session)
    with pytest.raises(ApprovalError, match="copy-mode only"):
        await queue_send(db_session, lead.id, "C")


async def test_queue_send_rejects_unsupported_platform(db_session):
    lead = await make_drafted_lead(
        db_session, source="hn", external_id="hn_123",
        url="https://news.ycombinator.com/item?id=123", community=None,
    )
    with pytest.raises(ApprovalError, match="copy mode"):
        await queue_send(db_session, lead.id, "A")


async def test_queue_send_rejects_double_queue(db_session):
    lead = await make_drafted_lead(db_session)
    await queue_send(db_session, lead.id, "A", rng=random.Random(1))
    with pytest.raises(ApprovalError, match="already has send"):
        await queue_send(db_session, lead.id, "A", rng=random.Random(1))


async def test_queue_send_requires_drafted_lead(db_session):
    lead = await make_drafted_lead(db_session)
    transition(lead, "skipped")
    await db_session.commit()
    with pytest.raises(ApprovalError, match="not awaiting approval"):
        await queue_send(db_session, lead.id, "A")


async def test_cancel_send_within_jitter_window(db_session):
    lead = await make_drafted_lead(db_session)
    send = await queue_send(db_session, lead.id, "A", rng=random.Random(1))
    assert await cancel_send(db_session, send.id) is True
    assert send.status == "cancelled"
    # lead still drafted -> owner can approve again
    assert lead.status == "drafted"
    again = await queue_send(db_session, lead.id, "B", rng=random.Random(1))
    assert again.status == "queued"


async def test_cancel_send_refuses_after_execution(db_session):
    lead = await make_drafted_lead(db_session)
    send = await queue_send(db_session, lead.id, "A", rng=random.Random(1))
    send.status = "sent"
    await db_session.commit()
    assert await cancel_send(db_session, send.id) is False
