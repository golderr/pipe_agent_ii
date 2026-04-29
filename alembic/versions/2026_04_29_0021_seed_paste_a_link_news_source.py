"""seed paste-a-link news source

Revision ID: 202604290021
Revises: 202604290020
Create Date: 2026-04-29 15:30:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604290021"
down_revision = "202604290020"
branch_labels = None
depends_on = None

PASTE_A_LINK_SOURCE_ID = "cc1dba8e-e80a-5941-9c08-d8948b306a73"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO news_sources (
            id,
            slug,
            name,
            base_url,
            collector_class,
            active,
            schedule_cron,
            schedule_timezone,
            config,
            market_id,
            jurisdiction_id
        )
        VALUES (
            '{PASTE_A_LINK_SOURCE_ID}',
            'news_paste_a_link',
            'Paste-a-link',
            'https://example.invalid',
            'PasteALinkCollector',
            true,
            NULL,
            NULL,
            '{{}}'::jsonb,
            (SELECT id FROM markets WHERE slug = 'unscoped' LIMIT 1),
            (
                SELECT id
                FROM jurisdictions
                WHERE slug = 'unknown_unscoped'
                  AND state = 'NA'
                LIMIT 1
            )
        )
        ON CONFLICT (slug) DO UPDATE
        SET
            name = EXCLUDED.name,
            base_url = EXCLUDED.base_url,
            collector_class = EXCLUDED.collector_class,
            active = EXCLUDED.active,
            schedule_cron = EXCLUDED.schedule_cron,
            schedule_timezone = EXCLUDED.schedule_timezone,
            config = EXCLUDED.config,
            market_id = EXCLUDED.market_id,
            jurisdiction_id = EXCLUDED.jurisdiction_id,
            updated_at = now()
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM news_sources WHERE slug = 'news_paste_a_link'")
