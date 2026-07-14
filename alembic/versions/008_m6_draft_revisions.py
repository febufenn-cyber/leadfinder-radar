"""M6C draft revision history.

Revision ID: 008
Revises: 007
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "draft_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("risk_flags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_gold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("guidance_sha256", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_draft_revisions_draft_id", "draft_revisions", ["draft_id"])
    op.create_index("ix_draft_revisions_lead_id", "draft_revisions", ["lead_id"])


def downgrade() -> None:
    op.drop_table("draft_revisions")
