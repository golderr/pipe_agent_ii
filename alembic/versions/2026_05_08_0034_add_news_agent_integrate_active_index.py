"""add active news agent integration job uniqueness

Revision ID: 202605080034
Revises: 202605070033
Create Date: 2026-05-08 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605080034"
down_revision = "202605070033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_scrape_jobs_one_active_news_agent_integrate_article",
        "scrape_jobs",
        [sa.text("(target_payload ->> 'article_id')")],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'news_agent_integrate' "
            "AND status IN ('queued', 'running') "
            "AND target_payload ? 'article_id'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scrape_jobs_one_active_news_agent_integrate_article",
        table_name="scrape_jobs",
    )
