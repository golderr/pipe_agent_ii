"""add socrata row metadata

Revision ID: 1608e6422a65
Revises: 8ee2234b3cce
Create Date: 2026-04-16 13:24:00.718460
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '1608e6422a65'
down_revision = '8ee2234b3cce'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_source_records",
        sa.Column("source_row_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "project_source_records",
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_source_records",
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_source_records",
        sa.Column("source_row_hash", sa.String(length=64), nullable=True),
    )

    op.add_column(
        "source_runs",
        sa.Column(
            "collection_mode",
            sa.String(length=20),
            nullable=False,
            server_default="full",
        ),
    )
    op.add_column(
        "source_runs",
        sa.Column("incremental_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_runs",
        sa.Column("source_min_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_runs",
        sa.Column("source_max_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_runs", "source_max_updated_at")
    op.drop_column("source_runs", "source_min_updated_at")
    op.drop_column("source_runs", "incremental_since")
    op.drop_column("source_runs", "collection_mode")
    op.drop_column("project_source_records", "source_row_hash")
    op.drop_column("project_source_records", "source_updated_at")
    op.drop_column("project_source_records", "source_created_at")
    op.drop_column("project_source_records", "source_row_id")
