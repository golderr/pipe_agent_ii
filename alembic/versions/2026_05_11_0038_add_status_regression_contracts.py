"""Add status regression review contracts.

Revision ID: 202605110038
Revises: 202605110037
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202605110038"
down_revision = "202605110037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE review_item_type_enum ADD VALUE IF NOT EXISTS "
        "'status_regression_review'"
    )
    op.add_column(
        "resolution_log",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("resolution_log", "metadata")
    # PostgreSQL enum values cannot be removed safely without rebuilding the type.
