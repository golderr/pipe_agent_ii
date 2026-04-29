"""extend scrape jobs for news jobs

Revision ID: 202604290020
Revises: 202604290019
Create Date: 2026-04-29 09:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604290020"
down_revision = "202604290019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scrape_jobs",
        sa.Column(
            "kind",
            sa.String(length=80),
            nullable=False,
            server_default="collector_run",
        ),
    )
    op.add_column(
        "scrape_jobs",
        sa.Column("target_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.alter_column(
        "scrape_jobs",
        "jurisdiction_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    op.drop_index("uq_scrape_jobs_one_active_per_source", table_name="scrape_jobs")
    op.create_index(
        "uq_scrape_jobs_one_active_collector",
        "scrape_jobs",
        ["jurisdiction_id", "source_name"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'collector_run' AND status IN ('queued', 'running')"
        ),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_scrape_jobs_one_active_news_scrape
        ON scrape_jobs (source_name)
        WHERE kind = 'news_scrape'
          AND status IN ('queued', 'running')
        """
    )
    op.create_index(
        "ix_scrape_jobs_kind_status",
        "scrape_jobs",
        ["kind", "status", "queued_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.add_column(
        "evidence",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        CREATE INDEX ix_evidence_active_project_resolution
        ON evidence (
            project_id,
            evidence_date DESC NULLS LAST,
            collected_at DESC,
            source_tier ASC
        )
        WHERE superseded_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_active_project_resolution", table_name="evidence")
    op.drop_column("evidence", "superseded_at")

    op.drop_index("ix_scrape_jobs_kind_status", table_name="scrape_jobs")
    op.drop_index("uq_scrape_jobs_one_active_news_scrape", table_name="scrape_jobs")
    op.drop_index("uq_scrape_jobs_one_active_collector", table_name="scrape_jobs")
    op.execute("DELETE FROM scrape_jobs WHERE jurisdiction_id IS NULL")
    op.alter_column(
        "scrape_jobs",
        "jurisdiction_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("scrape_jobs", "target_payload")
    op.drop_column("scrape_jobs", "kind")
    op.create_index(
        "uq_scrape_jobs_one_active_per_source",
        "scrape_jobs",
        ["jurisdiction_id", "source_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
