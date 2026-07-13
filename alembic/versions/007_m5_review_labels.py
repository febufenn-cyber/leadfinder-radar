"""M5 classifier review labels

Revision ID: 007
Revises: 006
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_post_id", sa.Integer(), nullable=False),
        sa.Column("pack", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("fit_score", sa.Integer(), nullable=True),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("predicted_positive", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("raw_post_id", name="uq_review_labels_raw_post_id"),
    )
    op.create_index("ix_review_labels_raw_post_id", "review_labels", ["raw_post_id"])
    op.create_index("ix_review_labels_pack", "review_labels", ["pack"])


def downgrade() -> None:
    op.drop_index("ix_review_labels_pack", table_name="review_labels")
    op.drop_index("ix_review_labels_raw_post_id", table_name="review_labels")
    op.drop_table("review_labels")
