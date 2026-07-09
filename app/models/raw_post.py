"""raw_posts — every post that passed the keyword filter (DESIGN §2 contract + pipeline fields)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RawPost(Base):
    __tablename__ = "raw_posts"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_raw_posts_source_external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # DESIGN §2 adapter contract
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    author_handle: Mapped[str | None] = mapped_column(Text)
    author_url: Mapped[str | None] = mapped_column(Text)
    community: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Pipeline fields
    pack: Mapped[str] = mapped_column(Text)
    matched_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
