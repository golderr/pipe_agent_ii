"""add news cache creation token buckets

Revision ID: 202604290022
Revises: 202604290021
Create Date: 2026-04-29 20:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604290022"
down_revision = "202604290021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "news_extractions",
        sa.Column("input_tokens_cache_creation", sa.Integer(), nullable=True),
    )
    op.add_column(
        "news_extraction_costs",
        sa.Column(
            "input_tokens_cache_creation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW news_extractions_summary
        WITH (security_invoker = false) AS
        SELECT
            id,
            article_id,
            pass,
            triggered_by,
            supersedes_extraction_id,
            prompt_id,
            prompt_version,
            model,
            model_provider,
            input_tokens_uncached,
            input_tokens_cached,
            output_tokens,
            cost_usd,
            latency_ms,
            parse_status,
            parse_error_text,
            triggered_by_user_id,
            created_at,
            input_tokens_cache_creation
        FROM news_extractions
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS news_extractions_summary")
    op.execute(
        """
        CREATE VIEW news_extractions_summary
        WITH (security_invoker = false) AS
        SELECT
            id,
            article_id,
            pass,
            triggered_by,
            supersedes_extraction_id,
            prompt_id,
            prompt_version,
            model,
            model_provider,
            input_tokens_uncached,
            input_tokens_cached,
            output_tokens,
            cost_usd,
            latency_ms,
            parse_status,
            parse_error_text,
            triggered_by_user_id,
            created_at
        FROM news_extractions
        """
    )
    op.execute("GRANT SELECT ON TABLE news_extractions_summary TO authenticated")
    op.drop_column("news_extraction_costs", "input_tokens_cache_creation")
    op.drop_column("news_extractions", "input_tokens_cache_creation")
