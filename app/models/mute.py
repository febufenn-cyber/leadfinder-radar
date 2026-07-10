"""mutes — owner-tapped keyword/community suppressions consulted by the prefilter."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Mute(Base):
    __tablename__ = "mutes"
    __table_args__ = (UniqueConstraint("kind", "value", "pack", name="uq_mutes_kind_value_pack"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)  # keyword | community
    value: Mapped[str] = mapped_column(Text)
    pack: Mapped[str | None] = mapped_column(Text)  # NULL = all packs
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
