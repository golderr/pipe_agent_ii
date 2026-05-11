"""clear stale news cost alerts

Revision ID: 202605110037
Revises: 202605100036
Create Date: 2026-05-11 18:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605110037"
down_revision = "202605100036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE system_alerts
        SET
            cleared_at = now(),
            cleared_reason = 'superseded_by_2026_05_07_news_cost_cap_update'
        WHERE alert_key IN (
            'news_daily_cost_warn_cap_reached',
            'news_daily_cost_hard_cap_reached'
        )
          AND raised_at < DATE '2026-05-08'
          AND cleared_at IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO system_alerts (
            alert_key,
            severity,
            scope,
            message,
            detail,
            raised_at,
            last_seen_at
        )
        SELECT
            'news_semantic_parse_failed',
            'warning',
            jsonb_build_object(
                'article_id', article_id::text,
                'extraction_id', extraction_id::text
            ),
            'News semantic interpretation did not produce usable structured output for extraction '
                || extraction_id::text
                || '; parse_status='
                || parse_status
                || '.',
            jsonb_build_object(
                'article_id', article_id::text,
                'extraction_id', extraction_id::text,
                'semantic_interpretation_id', id::text,
                'parse_status', parse_status,
                'parse_error_text', parse_error_text,
                'prompt_id', prompt_id,
                'prompt_version', prompt_version,
                'model', model,
                'provider', model_provider,
                'output_tokens', output_tokens,
                'cost_usd', cost_usd::text,
                'diagnostic', COALESCE(diagnostic, '{}'::jsonb)
            ),
            created_at,
            created_at
        FROM news_semantic_interpretations
        WHERE parse_status IN ('truncated', 'refused', 'parse_error', 'schema_invalid')
          AND created_at >= TIMESTAMPTZ '2026-05-11 00:00:00+00'
        ON CONFLICT (alert_key, (COALESCE(scope::text, '{}')))
            WHERE cleared_at IS NULL
        DO UPDATE SET
            severity = EXCLUDED.severity,
            message = EXCLUDED.message,
            detail = EXCLUDED.detail,
            last_seen_at = EXCLUDED.last_seen_at
        """
    )


def downgrade() -> None:
    # Operational alert cleanup/backfill is intentionally one-way.
    pass
