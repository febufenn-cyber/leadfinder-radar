"""sends — every queued/executed API-send (DESIGN §3.7 M4).

A row exists ONLY because the owner tapped approve on one specific draft:
approval_event_id is NOT NULL by design — the DoD invariant in table form.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Send(Base):
    __tablename__ = "sends"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    draft_id: Mapped[int] = mapped_column(Integer)
    approval_event_id: Mapped[int] = mapped_column(Integer)  # DoD: no send without approval
    platform: Mapped[str] = mapped_column(Text)  # reddit | threads
    channel: Mapped[str] = mapped_column(Text)  # comment | dm
    target_external_id: Mapped[str] = mapped_column(Text)  # t3_xxx / threads media id
    recipient: Mapped[str | None] = mapped_column(Text)  # dm recipient handle
    community: Mapped[str | None] = mapped_column(Text)  # for per-sub cooldown
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued")
    # queued | executing | sent | failed | halted | cancelled
    # 'executing' is the crash-safety marker: claimed-and-committed BEFORE the
    # API call so an interrupted send can never be re-posted (§0).
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_result_id: Mapped[str | None] = mapped_column(Text)  # posted comment id
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
