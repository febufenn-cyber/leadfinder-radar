"""drafts — reply variants per lead (DESIGN §3.5). Owner edits become gold samples."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    variant: Mapped[str] = mapped_column(Text)  # A | B | C
    channel: Mapped[str] = mapped_column(Text)  # comment | dm | comment+dm
    text: Mapped[str] = mapped_column(Text)
    risk_flags: Mapped[list] = mapped_column(JSONB, default=list)
    edited_text: Mapped[str | None] = mapped_column(Text)  # owner's inline edit
    is_gold: Mapped[bool] = mapped_column(Boolean, default=False)  # edited = gold sample
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
