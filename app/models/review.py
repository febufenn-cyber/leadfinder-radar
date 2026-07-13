"""Human review labels for classifier evaluation (DESIGN §6 / M5)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewLabel(Base):
    __tablename__ = "review_labels"
    __table_args__ = (
        UniqueConstraint("raw_post_id", name="uq_review_labels_raw_post_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(Integer, index=True)
    pack: Mapped[str] = mapped_column(Text, index=True)
    label: Mapped[str] = mapped_column(Text)  # demand | not_demand | skip
    fit_score: Mapped[int | None] = mapped_column(Integer)
    threshold: Mapped[int] = mapped_column(Integer)
    predicted_positive: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
