"""raw_posts score columns + llm_calls

Revision ID: 002
Revises: 001
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("raw_posts", sa.Column("fit_score", sa.Integer(), nullable=True))
    op.add_column("raw_posts", sa.Column("score", JSONB(), nullable=True))
    op.add_column(
        "raw_posts", sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("raw_post_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_llm_calls_ts", "llm_calls", ["ts"])


def downgrade() -> None:
    op.drop_table("llm_calls")
    op.drop_column("raw_posts", "classified_at")
    op.drop_column("raw_posts", "score")
    op.drop_column("raw_posts", "fit_score")
