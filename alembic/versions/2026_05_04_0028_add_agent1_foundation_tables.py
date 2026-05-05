"""add agent1 foundation tables

Revision ID: 202605040028
Revises: 202605010027
Create Date: 2026-05-04 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605040028"
down_revision = "202605010027"
branch_labels = None
depends_on = None

MIGRATION_ACTOR = "migration_202605040028"
NEWS_ARTICLE_CHUNK_EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_projects_location_gist
        ON projects USING GIST (location)
        WHERE location IS NOT NULL
        """
    )
    _create_retrieval_tables()
    _create_cost_tables()
    _migrate_news_cost_data()
    _configure_rls_and_grants()
    op.drop_table("news_extraction_costs")
    op.drop_table("news_cost_caps")


def downgrade() -> None:
    _recreate_legacy_news_cost_tables()
    _migrate_cost_data_to_legacy_news_tables()
    _drop_rls_and_grants()
    op.drop_table("llm_cost_usage")
    op.drop_table("cost_cap_overrides")
    op.drop_table("cost_caps")
    op.drop_table("news_reference_auto_applied")
    op.drop_table("news_article_chunks")
    op.execute("DROP INDEX IF EXISTS ix_projects_location_gist")


def _create_retrieval_tables() -> None:
    op.create_table(
        "news_article_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_index", sa.Integer(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("chunk_offset_start", sa.Integer(), nullable=True),
        sa.Column("chunk_offset_end", sa.Integer(), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("gate_source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "gate_source IN ("
            "'review_accept', "
            "'auto_applied_corroborating', "
            "'auto_applied_high_confidence'"
            ")",
            name="gate_source",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
    )
    op.execute(
        f"""
        ALTER TABLE news_article_chunks
        ALTER COLUMN embedding TYPE vector({NEWS_ARTICLE_CHUNK_EMBEDDING_DIMENSIONS})
        USING embedding::vector({NEWS_ARTICLE_CHUNK_EMBEDDING_DIMENSIONS})
        """
    )
    op.create_index(
        "ix_news_article_chunks_article_reference",
        "news_article_chunks",
        ["article_id", "reference_index"],
    )
    op.create_index(
        "ix_news_article_chunks_active_gate",
        "news_article_chunks",
        ["gate_source", sa.text("embedded_at DESC")],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_news_article_chunks_embedding_hnsw
        ON news_article_chunks
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
          AND superseded_at IS NULL
        """
    )

    op.create_table(
        "news_reference_auto_applied",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_index", sa.Integer(), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("gate", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "gate IN ('auto_applied_corroborating', 'auto_applied_high_confidence')",
            name="gate",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_run_id"], ["source_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "article_id",
            "reference_index",
            "source_run_id",
            "gate",
            name="uq_news_reference_auto_applied_reference_gate",
        ),
    )
    op.create_index(
        "ix_news_reference_auto_applied_article_reference",
        "news_reference_auto_applied",
        ["article_id", "reference_index"],
    )
    op.create_index(
        "ix_news_reference_auto_applied_source_run",
        "news_reference_auto_applied",
        ["source_run_id"],
    )
    op.create_index(
        "uq_news_reference_auto_applied_null_source_run_reference_gate",
        "news_reference_auto_applied",
        ["article_id", "reference_index", "gate"],
        unique=True,
        postgresql_where=sa.text("source_run_id IS NULL"),
    )


def _create_cost_tables() -> None:
    op.create_table(
        "cost_caps",
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("daily_warn_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("daily_hard_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "daily_warn_usd <= daily_hard_usd",
            name="warn_lte_hard",
        ),
        sa.PrimaryKeyConstraint("bucket", "effective_from"),
    )
    op.create_index(
        "ix_cost_caps_bucket_effective",
        "cost_caps",
        ["bucket", sa.text("effective_from DESC")],
    )

    op.create_table(
        "cost_cap_overrides",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("override_hard_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("override_warn_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("set_by_actor", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(set_by_user_id IS NOT NULL) <> (set_by_actor IS NOT NULL)",
            name="exactly_one_setter",
        ),
        sa.CheckConstraint(
            "override_warn_usd IS NULL OR override_warn_usd <= override_hard_usd",
            name="warn_lte_hard",
        ),
        sa.CheckConstraint(
            "effective_until > effective_from",
            name="valid_window",
        ),
    )
    op.create_index(
        "ix_cost_cap_overrides_bucket_until",
        "cost_cap_overrides",
        ["bucket", sa.text("effective_until DESC")],
    )

    op.create_table(
        "llm_cost_usage",
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("cost_date", sa.Date(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("call_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "input_tokens_uncached",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "input_tokens_cache_creation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "input_tokens_cached",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("spent_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("bucket", "cost_date", "capability", "provider", "model"),
    )
    op.create_index(
        "ix_llm_cost_usage_bucket_date",
        "llm_cost_usage",
        ["bucket", sa.text("cost_date DESC")],
    )


def _migrate_news_cost_data() -> None:
    op.execute(
        """
        INSERT INTO cost_caps (
            bucket,
            effective_from,
            effective_to,
            daily_warn_usd,
            daily_hard_usd,
            notes
        )
        SELECT
            'news',
            effective_date,
            (LEAD(effective_date) OVER (ORDER BY effective_date) - 1),
            daily_warn_usd,
            daily_hard_usd,
            'Migrated from news_cost_caps.'
        FROM news_cost_caps
        ON CONFLICT (bucket, effective_from) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO cost_caps (
            bucket,
            effective_from,
            effective_to,
            daily_warn_usd,
            daily_hard_usd,
            notes
        )
        SELECT
            'news',
            CURRENT_DATE,
            NULL,
            25.00,
            35.00,
            'Default AGENT.1 news cap.'
        WHERE NOT EXISTS (
            SELECT 1 FROM cost_caps WHERE bucket = 'news'
        )
        """
    )
    # The legacy table represented current cap config, not a durable override ledger.
    # Only window-active overrides are migrated; expired D.6 smoke bumps remain in
    # prior deployment/audit artifacts rather than becoming historical override rows.
    op.execute(
        f"""
        INSERT INTO cost_cap_overrides (
            bucket,
            override_hard_usd,
            override_warn_usd,
            effective_from,
            effective_until,
            set_by_user_id,
            set_by_actor,
            note
        )
        SELECT
            'news',
            override_hard_usd,
            NULL,
            COALESCE(created_at, now()),
            override_until,
            CASE
                WHEN override_set_by_user_id IS NOT NULL THEN override_set_by_user_id
                ELSE NULL
            END,
            CASE
                WHEN override_set_by_user_id IS NULL THEN '{MIGRATION_ACTOR}'
                ELSE NULL
            END,
            COALESCE(
                NULLIF(btrim(override_note), ''),
                'Migrated from news_cost_caps; original note was empty.'
            )
        FROM news_cost_caps
        WHERE override_hard_usd IS NOT NULL
          AND override_until IS NOT NULL
          AND override_until > now()
        """
    )
    op.execute(
        """
        INSERT INTO llm_cost_usage (
            bucket,
            cost_date,
            capability,
            provider,
            model,
            call_count,
            input_tokens_uncached,
            input_tokens_cache_creation,
            input_tokens_cached,
            output_tokens,
            spent_usd
        )
        SELECT
            'news',
            cost_date,
            pass,
            CASE
                WHEN pass = 'reserved' AND model = '_reservation_' THEN '_reservation_'
                ELSE 'anthropic'
            END,
            model,
            call_count,
            input_tokens_uncached,
            input_tokens_cache_creation,
            input_tokens_cached,
            output_tokens,
            cost_usd
        FROM news_extraction_costs
        ON CONFLICT (bucket, cost_date, capability, provider, model) DO UPDATE
        SET
            call_count = llm_cost_usage.call_count + EXCLUDED.call_count,
            input_tokens_uncached = (
                llm_cost_usage.input_tokens_uncached + EXCLUDED.input_tokens_uncached
            ),
            input_tokens_cache_creation = (
                llm_cost_usage.input_tokens_cache_creation
                + EXCLUDED.input_tokens_cache_creation
            ),
            input_tokens_cached = (
                llm_cost_usage.input_tokens_cached + EXCLUDED.input_tokens_cached
            ),
            output_tokens = llm_cost_usage.output_tokens + EXCLUDED.output_tokens,
            spent_usd = llm_cost_usage.spent_usd + EXCLUDED.spent_usd
        """
    )


def _configure_rls_and_grants() -> None:
    for table_name in ("cost_caps", "cost_cap_overrides", "llm_cost_usage"):
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO authenticated")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY authenticated_read_{table_name}
            ON {table_name}
            FOR SELECT
            TO authenticated
            USING (true)
            """
        )

    for table_name in ("news_article_chunks", "news_reference_auto_applied"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")


def _drop_rls_and_grants() -> None:
    for table_name in ("cost_caps", "cost_cap_overrides", "llm_cost_usage"):
        op.execute(f"DROP POLICY IF EXISTS authenticated_read_{table_name} ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT ON TABLE {table_name} FROM authenticated")

    for table_name in ("news_article_chunks", "news_reference_auto_applied"):
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")


def _recreate_legacy_news_cost_tables() -> None:
    op.create_table(
        "news_extraction_costs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cost_date", sa.Date(), nullable=False),
        sa.Column("pass", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "input_tokens_uncached",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "input_tokens_cache_creation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "input_tokens_cached",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("cost_date", "pass", "model"),
    )
    op.create_index(
        "ix_news_extraction_costs_cost_date",
        "news_extraction_costs",
        [sa.text("cost_date DESC")],
    )

    op.create_table(
        "news_cost_caps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("effective_date", sa.Date(), nullable=False, unique=True),
        sa.Column("daily_warn_usd", sa.Numeric(8, 2), nullable=False),
        sa.Column("daily_hard_usd", sa.Numeric(8, 2), nullable=False),
        sa.Column("override_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_hard_usd", sa.Numeric(8, 2), nullable=True),
        sa.Column("override_set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("override_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _migrate_cost_data_to_legacy_news_tables() -> None:
    op.execute(
        """
        INSERT INTO news_cost_caps (
            effective_date,
            daily_warn_usd,
            daily_hard_usd
        )
        SELECT effective_from, daily_warn_usd, daily_hard_usd
        FROM cost_caps
        WHERE bucket = 'news'
        ON CONFLICT (effective_date) DO NOTHING
        """
    )
    op.execute(
        """
        WITH latest_override AS (
            SELECT *
            FROM cost_cap_overrides
            WHERE bucket = 'news'
              AND effective_until > now()
            ORDER BY override_hard_usd DESC, effective_until DESC
            LIMIT 1
        )
        UPDATE news_cost_caps
        SET
            override_until = latest_override.effective_until,
            override_hard_usd = latest_override.override_hard_usd,
            override_set_by_user_id = latest_override.set_by_user_id,
            override_note = latest_override.note,
            updated_at = now()
        FROM latest_override
        WHERE news_cost_caps.effective_date = (
            SELECT max(effective_date) FROM news_cost_caps
        )
        """
    )
    op.execute(
        """
        INSERT INTO news_extraction_costs (
            cost_date,
            pass,
            model,
            call_count,
            input_tokens_uncached,
            input_tokens_cache_creation,
            input_tokens_cached,
            output_tokens,
            cost_usd
        )
        SELECT
            cost_date,
            capability,
            model,
            call_count,
            input_tokens_uncached,
            input_tokens_cache_creation,
            input_tokens_cached,
            output_tokens,
            spent_usd
        FROM llm_cost_usage
        WHERE bucket = 'news'
        ON CONFLICT (cost_date, pass, model) DO UPDATE
        SET
            call_count = news_extraction_costs.call_count + EXCLUDED.call_count,
            input_tokens_uncached = (
                news_extraction_costs.input_tokens_uncached
                + EXCLUDED.input_tokens_uncached
            ),
            input_tokens_cache_creation = (
                news_extraction_costs.input_tokens_cache_creation
                + EXCLUDED.input_tokens_cache_creation
            ),
            input_tokens_cached = (
                news_extraction_costs.input_tokens_cached + EXCLUDED.input_tokens_cached
            ),
            output_tokens = news_extraction_costs.output_tokens + EXCLUDED.output_tokens,
            cost_usd = news_extraction_costs.cost_usd + EXCLUDED.cost_usd
        """
    )
