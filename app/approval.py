"""Approval actions (DESIGN §3.6/§3.7) — pure DB logic, called by the Telegram bot.

Copy-mode only: approving returns the reply text + thread link for the owner to
post manually from his own account. The DoD invariant lives here: the approval
Event row is flushed BEFORE the lead is marked sent, so no send can ever exist
without its approval event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import IllegalTransition, Lead, transition
from app.models.mute import Mute
from app.models.raw_post import RawPost

log = logging.getLogger(__name__)


@dataclass
class CopyPayload:
    lead_id: int
    variant: str
    text: str
    url: str


class ApprovalError(Exception):
    """User-facing failure (unknown lead, wrong state, missing variant)."""


async def _get_lead(session: AsyncSession, lead_id: int) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise ApprovalError(f"lead #{lead_id} not found")
    return lead


async def _post_url(session: AsyncSession, lead: Lead) -> str:
    post = await session.get(RawPost, lead.raw_post_id)
    return post.url if post else ""


async def approve(session: AsyncSession, lead_id: int, variant: str) -> CopyPayload:
    """Owner tapped [Send X]: approval event FIRST, then drafted -> sent."""
    lead = await _get_lead(session, lead_id)
    draft = (
        await session.execute(
            select(Draft).where(Draft.lead_id == lead_id, Draft.variant == variant)
        )
    ).scalars().first()
    if draft is None:
        raise ApprovalError(f"lead #{lead_id} has no variant {variant}")

    session.add(
        Event(
            kind="approval",
            payload={
                "lead_id": lead_id,
                "draft_id": draft.id,
                "variant": variant,
                "mode": "copy",
                "edited": draft.edited_text is not None,
            },
        )
    )
    await session.flush()  # DoD: approval event exists before any 'sent' state

    try:
        transition(lead, "sent")
    except IllegalTransition as exc:
        raise ApprovalError(str(exc)) from exc
    lead.chosen_draft_id = draft.id
    url = await _post_url(session, lead)
    await session.commit()
    return CopyPayload(
        lead_id=lead_id, variant=variant, text=draft.edited_text or draft.text, url=url
    )


async def save_edit(session: AsyncSession, lead_id: int, new_text: str) -> CopyPayload:
    """Owner replied with edited text: store as gold sample, then approve it."""
    await _get_lead(session, lead_id)  # existence check; approve() re-validates state
    draft = (
        await session.execute(
            select(Draft).where(Draft.lead_id == lead_id).order_by(Draft.variant)
        )
    ).scalars().first()
    if draft is None:
        raise ApprovalError(f"lead #{lead_id} has no drafts to edit")
    draft.edited_text = new_text.strip()
    draft.is_gold = True  # the learning loop's gold set (DESIGN §3.6/§6)
    await session.flush()
    return await approve(session, lead_id, draft.variant)


async def skip(session: AsyncSession, lead_id: int) -> None:
    lead = await _get_lead(session, lead_id)
    try:
        transition(lead, "skipped")
    except IllegalTransition as exc:
        raise ApprovalError(str(exc)) from exc
    session.add(Event(kind="lead_skipped", payload={"lead_id": lead_id}))
    await session.commit()


async def add_mute(session: AsyncSession, kind: str, value: str, pack: str | None) -> bool:
    """Insert a mute; False if it already existed."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(Mute)
        .values(kind=kind, value=value.lower(), pack=pack)
        .on_conflict_do_nothing(constraint="uq_mutes_kind_value_pack")
        .returning(Mute.id)
    )
    inserted = (await session.execute(stmt)).scalar()
    session.add(
        Event(kind="mute_added", payload={"kind": kind, "value": value.lower(), "pack": pack})
    )
    await session.commit()
    return inserted is not None
