"""indexes for backlog drain + cost-per-lead join

Revision ID: 004
Revises: 003
Create Date: 2026-07-10
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_raw_posts_backlog",
        "raw_posts",
        ["pack", "classified_at", "alerted_at"],
    )
    op.create_index("ix_llm_calls_raw_post_id", "llm_calls", ["raw_post_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_raw_post_id")
    op.drop_index("ix_raw_posts_backlog")
