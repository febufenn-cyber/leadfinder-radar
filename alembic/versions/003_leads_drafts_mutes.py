"""leads + drafts + mutes

Revision ID: 003
Revises: 002
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_post_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("pack", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="surfaced"),
        sa.Column("chosen_draft_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("approval_pushed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_leads_status", "leads", ["status"])

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("risk_flags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("is_gold", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_drafts_lead_id", "drafts", ["lead_id"])

    op.create_table(
        "mutes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("pack", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("kind", "value", "pack", name="uq_mutes_kind_value_pack"),
    )


def downgrade() -> None:
    op.drop_table("mutes")
    op.drop_table("drafts")
    op.drop_table("leads")
