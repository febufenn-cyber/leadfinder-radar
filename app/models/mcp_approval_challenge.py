"""Short-lived single-use MCP approval challenges (M6D)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MCPApprovalChallenge(Base):
    __tablename__ = "mcp_approval_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    draft_id: Mapped[int] = mapped_column(Integer)
    variant: Mapped[str] = mapped_column(Text)
    code_salt: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(Text)
    draft_sha256: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
