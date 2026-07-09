"""raw_posts + events

Revision ID: 001
Revises:
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("author_handle", sa.Text(), nullable=True),
        sa.Column("author_url", sa.Text(), nullable=True),
        sa.Column("community", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("pack", sa.Text(), nullable=False),
        sa.Column("matched_keywords", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("source", "external_id", name="uq_raw_posts_source_external_id"),
    )
    op.create_index("ix_raw_posts_fetched_at", "raw_posts", ["fetched_at"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_events_kind_ts", "events", ["kind", "ts"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("raw_posts")
