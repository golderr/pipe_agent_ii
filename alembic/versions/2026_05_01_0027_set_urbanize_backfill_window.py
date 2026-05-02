"""set urbanize backfill window

Revision ID: 202605010027
Revises: 202605010026
Create Date: 2026-05-01 16:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605010027"
down_revision = "202605010026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE news_sources
        SET
            config = jsonb_set(
                COALESCE(config, '{}'::jsonb),
                '{backfill_window_days}',
                '56'::jsonb,
                true
            ),
            updated_at = now()
        WHERE slug = 'urbanize_la'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE news_sources
        SET
            config = config - 'backfill_window_days',
            updated_at = now()
        WHERE slug = 'urbanize_la'
        """
    )
