"""enforce one active scrape job per source

Revision ID: 202604270015
Revises: 202604270014
Create Date: 2026-04-27 22:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604270015"
down_revision = "202604270014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
          SELECT
            id,
            row_number() OVER (
              PARTITION BY jurisdiction_id, source_name
              ORDER BY queued_at ASC, id ASC
            ) AS active_rank
          FROM scrape_jobs
          WHERE status IN ('queued', 'running')
        )
        UPDATE scrape_jobs
        SET
          status = 'cancelled',
          completed_at = COALESCE(completed_at, now()),
          error_text = COALESCE(
            error_text,
            'Cancelled by migration 202604270015 after duplicate active scrape job.'
          ),
          progress = jsonb_build_object('message', 'Cancelled duplicate active scrape job.')
        WHERE id IN (SELECT id FROM ranked WHERE active_rank > 1)
        """
    )
    op.create_index(
        "uq_scrape_jobs_one_active_per_source",
        "scrape_jobs",
        ["jurisdiction_id", "source_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_scrape_jobs_one_active_per_source", table_name="scrape_jobs")
