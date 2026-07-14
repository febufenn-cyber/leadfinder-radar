"""M6D Telegram-gated approval challenges.

Revision ID: 009
Revises: 008
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_approval_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("code_salt", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("draft_sha256", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_mcp_approval_challenges_lead_id",
        "mcp_approval_challenges",
        ["lead_id"],
    )
    op.create_index(
        "ix_mcp_approval_challenges_expires_at",
        "mcp_approval_challenges",
        ["expires_at"],
    )
    op.create_index(
        "uq_mcp_active_approval_challenge",
        "mcp_approval_challenges",
        ["lead_id", "variant"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("mcp_approval_challenges")
