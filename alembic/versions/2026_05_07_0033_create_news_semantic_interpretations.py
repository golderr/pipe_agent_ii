"""create news semantic interpretation audit table

Revision ID: 202605070033
Revises: 202605070032
Create Date: 2026-05-07 16:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605070033"
down_revision = "202605070032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_semantic_interpretations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "model_provider",
            sa.Text(),
            nullable=False,
            server_default="anthropic",
        ),
        sa.Column("input_tokens_uncached", sa.Integer(), nullable=True),
        sa.Column("input_tokens_cache_creation", sa.Integer(), nullable=True),
        sa.Column("input_tokens_cached", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("output_json", postgresql.JSONB(), nullable=True),
        sa.Column("raw_response_text", sa.Text(), nullable=True),
        sa.Column("parse_status", sa.Text(), nullable=False, server_default="ok"),
        sa.Column("parse_error_text", sa.Text(), nullable=True),
        sa.Column("diagnostic", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["news_articles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["news_extractions.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_news_semantic_interpretations_article_id_created_at",
        "news_semantic_interpretations",
        ["article_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_news_semantic_interpretations_extraction_id",
        "news_semantic_interpretations",
        ["extraction_id"],
    )
    op.create_index(
        "ix_news_semantic_interpretations_prompt_id_version",
        "news_semantic_interpretations",
        ["prompt_id", "prompt_version"],
    )
    op.create_index(
        "ix_news_semantic_interpretations_parse_status_created_at",
        "news_semantic_interpretations",
        ["parse_status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_semantic_interpretations_parse_status_created_at",
        table_name="news_semantic_interpretations",
    )
    op.drop_index(
        "ix_news_semantic_interpretations_prompt_id_version",
        table_name="news_semantic_interpretations",
    )
    op.drop_index(
        "ix_news_semantic_interpretations_extraction_id",
        table_name="news_semantic_interpretations",
    )
    op.drop_index(
        "ix_news_semantic_interpretations_article_id_created_at",
        table_name="news_semantic_interpretations",
    )
    op.drop_table("news_semantic_interpretations")
