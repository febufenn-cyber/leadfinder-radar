"""Leads state machine (DESIGN §3.8) + drafts/mutes schema."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.draft import Draft
from app.models.lead import ILLEGAL_TRANSITION, Lead, transition
from app.models.mute import Mute
from app.db.session import insert_new_posts
from tests.conftest import make_post_row


async def make_lead(session, **overrides) -> Lead:
    (post,) = await insert_new_posts(session, [make_post_row()])
    lead = Lead(raw_post_id=post.id, pack="robofox_web", **overrides)
    session.add(lead)
    await session.flush()
    return lead


async def test_lead_defaults_to_surfaced(db_session):
    lead = await make_lead(db_session)
    assert lead.status == "surfaced"


async def test_legal_transition_chain(db_session):
    lead = await make_lead(db_session)
    for status in ["drafted", "sent", "replied", "conversation", "won"]:
        transition(lead, status)
    assert lead.status == "won"


async def test_illegal_transition_raises(db_session):
    lead = await make_lead(db_session)
    with pytest.raises(ILLEGAL_TRANSITION):
        transition(lead, "won")  # surfaced -> won skips the funnel


async def test_skip_from_drafted(db_session):
    lead = await make_lead(db_session)
    transition(lead, "drafted")
    transition(lead, "skipped")
    assert lead.status == "skipped"


async def test_one_lead_per_raw_post(db_session):
    lead = await make_lead(db_session)
    db_session.add(Lead(raw_post_id=lead.raw_post_id, pack="robofox_web"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_draft_rows_link_to_lead(db_session):
    lead = await make_lead(db_session)
    db_session.add(
        Draft(lead_id=lead.id, variant="A", channel="comment", text="hi", risk_flags=[])
    )
    await db_session.flush()
    draft = (await db_session.execute(select(Draft))).scalars().one()
    assert draft.lead_id == lead.id
    assert draft.is_gold is False


async def test_mute_uniqueness(db_session):
    db_session.add(Mute(kind="community", value="smallbusiness", pack="robofox_web"))
    await db_session.flush()
    db_session.add(Mute(kind="community", value="smallbusiness", pack="robofox_web"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
