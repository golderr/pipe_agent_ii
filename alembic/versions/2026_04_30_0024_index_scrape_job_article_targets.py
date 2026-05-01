"""index scrape jobs by article target payload

Revision ID: 202604300024
Revises: 202604300023
Create Date: 2026-04-30 17:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604300024"
down_revision = "202604300023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_scrape_jobs_article_id_kind_status_queued_at",
        "scrape_jobs",
        [
            sa.text("(target_payload ->> 'article_id')"),
            "kind",
            "status",
            sa.text("queued_at DESC"),
        ],
        unique=False,
        postgresql_where=sa.text("target_payload ? 'article_id'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scrape_jobs_article_id_kind_status_queued_at",
        table_name="scrape_jobs",
    )
