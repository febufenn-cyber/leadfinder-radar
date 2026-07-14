"""Archived draft snapshots created before an M6 redraft replaces active variants."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DraftRevision(Base):
    __tablename__ = "draft_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(Integer, index=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    variant: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    edited_text: Mapped[str | None] = mapped_column(Text)
    risk_flags: Mapped[list] = mapped_column(JSONB, default=list)
    is_gold: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text)
    guidance_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
