"""leads — the CRM state machine (DESIGN §3.8).

surfaced → drafted → sent → replied → conversation → won|lost|no_response
(+ `skipped`: owner declined at the approval gate — pragmatic addition.)
Follow-ups are human; nothing in code advances a lead past `sent` except
reply detection (M4) and the owner.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "surfaced": {"drafted", "skipped"},
    "drafted": {"sent", "skipped"},
    "sent": {"replied", "no_response", "lost"},
    "replied": {"conversation", "lost", "no_response"},
    "conversation": {"won", "lost", "no_response"},
    # terminal: won, lost, no_response, skipped
}


class IllegalTransition(ValueError):
    pass


ILLEGAL_TRANSITION = IllegalTransition  # importable alias for tests/callers


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(Integer, unique=True)
    pack: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="surfaced")
    chosen_draft_id: Mapped[int | None] = mapped_column(Integer)
    draft_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    approval_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def transition(lead: Lead, to: str) -> Lead:
    allowed = ALLOWED_TRANSITIONS.get(lead.status, set())
    if to not in allowed:
        raise IllegalTransition(f"lead {lead.id}: {lead.status} -> {to} not allowed")
    lead.status = to
    return lead
