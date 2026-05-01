"""seed urbanize la news source

Revision ID: 202605010025
Revises: 202604300024
Create Date: 2026-05-01 08:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605010025"
down_revision = "202604300024"
branch_labels = None
depends_on = None

URBANIZE_LA_SOURCE_ID = "2d95c19a-7b95-5bbf-8b26-d7f2750152c4"


def upgrade() -> None:
    op.execute(
        """
        UPDATE news_sources
        SET
            active = false,
            schedule_cron = NULL,
            schedule_timezone = 'America/Los_Angeles',
            updated_at = now()
        WHERE slug = 'bizjournals_la'
        """
    )
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
            '{URBANIZE_LA_SOURCE_ID}',
            'urbanize_la',
            'Urbanize LA',
            'https://la.urbanize.city',
            'PoliteNewsCollector',
            true,
            '30 7 * * *',
            'America/Los_Angeles',
            '{{
                "fetch_path": "polite",
                "hosts": ["la.urbanize.city"],
                "rss_urls": ["https://la.urbanize.city/rss.xml"],
                "sitemap_urls": ["https://la.urbanize.city/sitemap.xml"],
                "robots_url": "https://la.urbanize.city/robots.txt",
                "robots_cache_ttl_seconds": 86400,
                "rate_limit_seconds": 2,
                "source_strategy_doc": "docs/sources/news/urbanize_la.md",
                "user_agent": "Mozilla/5.0 (compatible; TCGPipelineTracker/0.1; +https://tcg-pipeline.vercel.app)"
            }}'::jsonb,
            NULL,
            NULL
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
    op.execute("DELETE FROM news_sources WHERE slug = 'urbanize_la'")
    op.execute(
        """
        UPDATE news_sources
        SET
            active = true,
            schedule_cron = '0 13 * * *',
            schedule_timezone = 'America/Los_Angeles',
            updated_at = now()
        WHERE slug = 'bizjournals_la'
        """
    )
