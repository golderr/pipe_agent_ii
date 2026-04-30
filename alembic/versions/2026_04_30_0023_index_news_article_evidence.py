"""index active news article evidence by article id

Revision ID: 202604300023
Revises: 202604290022
Create Date: 2026-04-30 15:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604300023"
down_revision = "202604290022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_evidence_news_article_id_active",
        "evidence",
        [sa.text("(raw_data ->> 'article_id')")],
        unique=False,
        postgresql_where=sa.text(
            "source_type = 'news_article' AND superseded_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_news_article_id_active", table_name="evidence")
