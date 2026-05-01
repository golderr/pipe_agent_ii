"""add news source run counters

Revision ID: 202605010026
Revises: 202605010025
Create Date: 2026-05-01 11:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605010026"
down_revision = "202605010025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_runs",
        sa.Column(
            "block_like_failure_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "source_runs",
        sa.Column(
            "transient_failure_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "source_runs",
        sa.Column(
            "cost_cap_skipped_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("source_runs", "cost_cap_skipped_count")
    op.drop_column("source_runs", "transient_failure_count")
    op.drop_column("source_runs", "block_like_failure_count")
