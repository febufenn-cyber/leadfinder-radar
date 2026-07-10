"""Approval actions (DESIGN §3.6/§3.7): copy-mode approve, edits as gold, skip, mutes.

The DoD invariant: no send without an approval event row — approve() writes the
event BEFORE the lead can reach 'sent'.
"""

import pytest
from sqlalchemy import select

from app.approval import ApprovalError, add_mute, approve, save_edit, skip
from app.db.session import insert_new_posts
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead, transition
from app.models.mute import Mute
from tests.conftest import make_post_row


async def make_drafted_lead(session) -> Lead:
    (post,) = await insert_new_posts(session, [make_post_row()])
    lead = Lead(raw_post_id=post.id, pack="robofox_web")
    session.add(lead)
    await session.flush()
    transition(lead, "drafted")
    session.add(Draft(lead_id=lead.id, variant="A", channel="comment", text="draft A", risk_flags=[]))
    session.add(Draft(lead_id=lead.id, variant="B", channel="dm", text="draft B", risk_flags=[]))
    await session.commit()
    return lead


async def test_approve_returns_copy_payload_and_marks_sent(db_session):
    lead = await make_drafted_lead(db_session)
    payload = await approve(db_session, lead.id, "B")
    assert payload.text == "draft B"
    assert payload.url.startswith("https://www.reddit.com/")
    assert lead.status == "sent"
    assert lead.chosen_draft_id is not None
    events = (await db_session.execute(select(Event).where(Event.kind == "approval"))).scalars().all()
    assert len(events) == 1
    assert events[0].payload["variant"] == "B"
    assert events[0].payload["mode"] == "copy"


async def test_approve_twice_fails_cleanly(db_session):
    lead = await make_drafted_lead(db_session)
    await approve(db_session, lead.id, "A")
    with pytest.raises(ApprovalError):
        await approve(db_session, lead.id, "A")


async def test_approve_unknown_variant_or_lead(db_session):
    lead = await make_drafted_lead(db_session)
    with pytest.raises(ApprovalError):
        await approve(db_session, lead.id, "C")
    with pytest.raises(ApprovalError):
        await approve(db_session, 99999, "A")


async def test_save_edit_stores_gold_on_the_chosen_variant(db_session):
    """Editing B must gold-stamp B — stamping A would poison the gold set."""
    lead = await make_drafted_lead(db_session)
    payload = await save_edit(db_session, lead.id, "  my own words  ", variant="B")
    assert payload.text == "my own words"
    assert payload.variant == "B"
    assert lead.status == "sent"
    draft_b = (
        await db_session.execute(select(Draft).where(Draft.variant == "B"))
    ).scalars().one()
    assert draft_b.is_gold is True
    assert draft_b.edited_text == "my own words"
    draft_a = (
        await db_session.execute(select(Draft).where(Draft.variant == "A"))
    ).scalars().one()
    assert draft_a.is_gold is False  # untouched
    events = (await db_session.execute(select(Event).where(Event.kind == "approval"))).scalars().all()
    assert events[0].payload["edited"] is True


async def test_save_edit_unknown_variant_fails(db_session):
    lead = await make_drafted_lead(db_session)
    with pytest.raises(ApprovalError):
        await save_edit(db_session, lead.id, "text", variant="C")


async def test_skip_marks_skipped(db_session):
    lead = await make_drafted_lead(db_session)
    await skip(db_session, lead.id)
    assert lead.status == "skipped"


async def test_add_mute_dedupes(db_session):
    assert await add_mute(db_session, "community", "SmallBusiness", "robofox_web") is True
    assert await add_mute(db_session, "community", "smallbusiness", "robofox_web") is False
    mutes = (await db_session.execute(select(Mute))).scalars().all()
    assert len(mutes) == 1
    assert mutes[0].value == "smallbusiness"  # normalized lowercase