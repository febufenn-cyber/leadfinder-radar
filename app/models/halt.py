"""halts — auto-halt ledger (DESIGN §3.7): a mod removal/warning blocks ALL
sending until the owner manually clears it (scripts/clear_halt.py)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Halt(Base):
    __tablename__ = "halts"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(Text)  # reddit | threads | all
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
