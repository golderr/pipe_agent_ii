"""create news research tables

Revision ID: 202604290019
Revises: 202604280018
Create Date: 2026-04-29 09:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604290019"
down_revision = "202604280018"
branch_labels = None
depends_on = None


NEWS_SUMMARY_READER_ROLE = "news_summary_reader"

AUTHENTICATED_READ_TABLES = (
    "news_sources",
    "news_extraction_costs",
    "news_cost_caps",
    "news_signal_flag_registry",
    "system_alerts",
    "worker_heartbeats",
    "news_admin_actions",
)

SUMMARY_VIEW_BASE_TABLES = (
    "news_articles",
    "news_extractions",
    "news_project_references",
)

PRIVATE_TABLES = (
    "service_credentials",
    "service_credential_validations",
)

UNSCOPED_MARKET_ID = "981f4c1e-c3d8-5e44-8022-bbaf87a72504"
UNSCOPED_JURISDICTION_ID = "6bc4fd89-41e8-5858-85c2-9dbf3db6daf6"

SIGNAL_FLAGS = (
    (
        "groundbreaking_announced",
        "Groundbreaking announced",
        "milestone",
        "Article says work has officially started or a groundbreaking ceremony occurred.",
        ["broke ground", "groundbreaking", "construction began"],
    ),
    (
        "topped_out",
        "Topped out",
        "milestone",
        "Article says the structure reached its final height.",
        ["topped out", "reached full height"],
    ),
    (
        "delivered",
        "Delivered",
        "milestone",
        "Article says the project opened, was completed, or delivered units.",
        ["opened", "completed", "delivered"],
    ),
    (
        "pre_leasing_open",
        "Pre-leasing open",
        "milestone",
        "Article says leasing has started before delivery.",
        ["pre-leasing", "now leasing", "leasing has begun"],
    ),
    (
        "sales_or_leasing_center_open",
        "Sales or leasing center open",
        "milestone",
        "Article says a sales office, leasing center, or model unit is open.",
        ["sales center", "leasing center", "model units"],
    ),
    (
        "construction_financing_announced",
        "Construction financing announced",
        "milestone",
        "Article reports a construction loan or construction financing package.",
        ["construction loan", "construction financing"],
    ),
    (
        "equity_financing_announced",
        "Equity financing announced",
        "milestone",
        "Article reports equity funding, a joint venture, or an investment partner.",
        ["equity partner", "joint venture", "equity financing"],
    ),
    (
        "community_opposition",
        "Community opposition",
        "risk",
        "Article describes organized opposition, neighborhood objections, or public backlash.",
        ["opposition", "neighbors objected", "community concerns"],
    ),
    (
        "lawsuit_filed",
        "Lawsuit filed",
        "risk",
        "Article says litigation was filed against the project or entitlement.",
        ["lawsuit", "sued", "legal challenge"],
    ),
    (
        "lawsuit_resolved",
        "Lawsuit resolved",
        "risk",
        "Article says project litigation was resolved, settled, dismissed, or withdrawn.",
        ["settled", "dismissed", "resolved lawsuit"],
    ),
    (
        "appeal_filed",
        "Appeal filed",
        "risk",
        "Article says an appeal was filed against an approval or entitlement.",
        ["appeal filed", "appealed approval"],
    ),
    (
        "appeal_resolved",
        "Appeal resolved",
        "risk",
        "Article says an appeal was denied, withdrawn, upheld, or otherwise resolved.",
        ["appeal denied", "appeal withdrawn", "upheld approval"],
    ),
    (
        "stalled_indicator",
        "Stalled indicator",
        "risk",
        "Article suggests the project is delayed, paused, shelved, or not moving forward.",
        ["stalled", "on hold", "delayed"],
    ),
    (
        "developer_change",
        "Developer change",
        "risk",
        "Article says project sponsorship changed through sale, assignment, or replacement.",
        ["sold the project", "new developer", "developer changed"],
    ),
    (
        "naming_change",
        "Naming change",
        "project_change",
        "Article uses or announces a new project name.",
        ["renamed", "now called", "branded as"],
    ),
    (
        "unit_count_change",
        "Unit count change",
        "project_change",
        "Article reports a unit count that differs from prior tracked evidence.",
        ["units", "apartments", "homes"],
    ),
    (
        "product_type_change",
        "Product type change",
        "project_change",
        "Article reports a different housing product type or tenure.",
        ["condominiums", "apartments", "townhomes"],
    ),
    (
        "delivery_date_changed",
        "Delivery date changed",
        "project_change",
        "Article reports a revised opening, completion, or delivery date.",
        ["delayed until", "expected to open", "scheduled for completion"],
    ),
    (
        "affordable_inclusionary_component",
        "Affordable component",
        "project_change",
        "Article identifies affordable, workforce, inclusionary, or income-restricted units.",
        ["affordable units", "income-restricted", "workforce housing"],
    ),
    (
        "entitlement_change",
        "Entitlement change",
        "project_change",
        "Article reports an entitlement approval, denial, amendment, or resubmittal.",
        ["approved", "entitled", "resubmitted"],
    ),
    (
        "mention_only",
        "Mention only",
        "meta",
        "Article mentions the project but does not provide material pipeline facts.",
        ["mentioned", "nearby project", "portfolio includes"],
    ),
    (
        "speculative",
        "Speculative",
        "meta",
        "Article language is tentative, rumored, or explicitly speculative.",
        ["could", "may", "rumored"],
    ),
    (
        "correction_or_retraction",
        "Correction or retraction",
        "meta",
        "Article corrects, updates, or retracts an earlier report.",
        ["correction", "updated", "retraction"],
    ),
    (
        "prior_phase_delivered_same_site",
        "Prior phase delivered same site",
        "meta",
        "Article refers to a completed earlier phase at the same site.",
        ["first phase", "previous phase", "earlier phase"],
    ),
    (
        "land_assembly_incomplete",
        "Land assembly incomplete",
        "meta",
        "Article suggests site control or land assembly remains incomplete.",
        ["land assembly", "site control", "acquire remaining parcels"],
    ),
)


def upgrade() -> None:
    _create_news_summary_reader_role()
    _create_tables()
    _add_article_extraction_foreign_keys()
    _create_summary_views()
    _seed_initial_rows()
    _configure_rls_and_grants()


def downgrade() -> None:
    _drop_rls_and_grants()
    op.execute("DROP VIEW IF EXISTS news_project_references_summary")
    op.execute("DROP VIEW IF EXISTS news_extractions_summary")
    op.execute("DROP VIEW IF EXISTS news_articles_summary")
    op.drop_constraint(
        "fk_news_articles_current_extraction_id_news_extractions",
        "news_articles",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_news_articles_triage_extraction_id_news_extractions",
        "news_articles",
        type_="foreignkey",
    )
    for table_name in (
        "news_admin_actions",
        "service_credential_validations",
        "worker_heartbeats",
        "system_alerts",
        "service_credentials",
        "news_signal_flag_registry",
        "news_cost_caps",
        "news_extraction_costs",
        "news_project_references",
        "news_extractions",
        "news_articles",
        "news_sources",
    ):
        op.drop_table(table_name)
    op.execute(f"DROP ROLE IF EXISTS {NEWS_SUMMARY_READER_ROLE}")


def _create_news_summary_reader_role() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = '{NEWS_SUMMARY_READER_ROLE}'
            ) THEN
                CREATE ROLE {NEWS_SUMMARY_READER_ROLE} NOLOGIN;
            END IF;
        END $$;
        """
    )


def _create_tables() -> None:
    op.create_table(
        "news_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("collector_class", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("schedule_cron", sa.Text(), nullable=True),
        sa.Column("schedule_timezone", sa.Text(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("jurisdiction_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["jurisdiction_id"], ["jurisdictions.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_news_sources_active",
        "news_sources",
        ["active"],
    )

    op.create_table(
        "news_articles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("news_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url_canonical", sa.Text(), nullable=False),
        sa.Column("url_original", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("fetch_status", sa.Text(), nullable=False),
        sa.Column("fetch_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "first_attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetch_error_text", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("raw_html_hash", sa.String(length=64), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_text_hash", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("byline_author", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_section", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("external_article_id", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("paywall_state", sa.Text(), nullable=True),
        sa.Column("structural_signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("structural_signals_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triage_status", sa.Text(), nullable=True),
        sa.Column("triage_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triage_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "current_extraction_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("ingest_method", sa.Text(), nullable=False),
        sa.Column("ingested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["news_source_id"], ["news_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_news_articles_news_source_id", "news_articles", ["news_source_id"])
    op.create_index(
        "ix_news_articles_published_at",
        "news_articles",
        [sa.text("published_at DESC NULLS LAST")],
    )
    op.create_index("ix_news_articles_fetch_status", "news_articles", ["fetch_status"])
    op.create_index("ix_news_articles_triage_status", "news_articles", ["triage_status"])
    op.create_index("ix_news_articles_body_text_hash", "news_articles", ["body_text_hash"])

    op.create_table(
        "news_extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pass", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("supersedes_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_provider", sa.Text(), nullable=False, server_default="anthropic"),
        sa.Column("input_tokens_uncached", sa.Integer(), nullable=True),
        sa.Column("input_tokens_cached", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_response_text", sa.Text(), nullable=True),
        sa.Column("parse_status", sa.Text(), nullable=False),
        sa.Column("parse_error_text", sa.Text(), nullable=True),
        sa.Column("diagnostic", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("triggered_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["supersedes_extraction_id"],
            ["news_extractions.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_news_extractions_article_id_created_at",
        "news_extractions",
        ["article_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_news_extractions_prompt_id_version",
        "news_extractions",
        ["prompt_id", "prompt_version"],
    )
    op.create_index(
        "ix_news_extractions_pass_triggered_by",
        "news_extractions",
        ["pass", "triggered_by"],
    )

    op.create_table(
        "news_project_references",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_index", sa.Integer(), nullable=False),
        sa.Column("candidate_name", sa.Text(), nullable=True),
        sa.Column("candidate_address", sa.Text(), nullable=True),
        sa.Column("candidate_developer", sa.Text(), nullable=True),
        sa.Column("candidate_unit_total", sa.Integer(), nullable=True),
        sa.Column("candidate_unit_affordable", sa.Integer(), nullable=True),
        sa.Column("candidate_unit_market_rate", sa.Integer(), nullable=True),
        sa.Column("candidate_product_type", sa.Text(), nullable=True),
        sa.Column("candidate_age_restriction", sa.Text(), nullable=True),
        sa.Column("candidate_status_signal", sa.Text(), nullable=True),
        sa.Column("candidate_delivery_year_text", sa.Text(), nullable=True),
        sa.Column("candidate_delivery_year_normalized", sa.Date(), nullable=True),
        sa.Column("candidate_signal_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("candidate_identifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("candidate_neighborhood", sa.Text(), nullable=True),
        sa.Column("candidate_lat", sa.Float(), nullable=True),
        sa.Column("candidate_lng", sa.Float(), nullable=True),
        sa.Column("candidate_confidence", sa.Text(), nullable=False),
        sa.Column("passage_excerpts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("match_status", sa.Text(), nullable=True),
        sa.Column("matched_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("match_reason", sa.Text(), nullable=True),
        sa.Column("match_candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("match_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manual_relink_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manual_relink_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_relink_note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["extraction_id"], ["news_extractions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matched_evidence_id"], ["evidence.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["review_item_id"], ["review_items.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("extraction_id", "reference_index"),
    )
    op.create_index("ix_news_project_references_article_id", "news_project_references", ["article_id"])
    op.create_index(
        "ix_news_project_references_matched_project_id",
        "news_project_references",
        ["matched_project_id"],
    )
    op.create_index(
        "ix_news_project_references_match_status",
        "news_project_references",
        ["match_status"],
    )

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

    op.create_table(
        "news_signal_flag_registry",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("flag_key", sa.Text(), nullable=False, unique=True),
        sa.Column("display_label", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("example_phrases", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_news_signal_flag_registry_active",
        "news_signal_flag_registry",
        ["active"],
    )

    op.create_table(
        "service_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("payload_kid", sa.Text(), nullable=False),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "system_alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("alert_key", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cleared_reason", sa.Text(), nullable=True),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_system_alerts_active_key_scope
        ON system_alerts (alert_key, (COALESCE(scope::text, '{}')))
        WHERE cleared_at IS NULL
        """
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.Text(), primary_key=True),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "process_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("active_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_job_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        "service_credential_validations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("credential_slug", sa.Text(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        sa.Column("validated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("validated_by_process", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_service_credential_validations_credential_validated_at",
        "service_credential_validations",
        ["credential_slug", sa.text("validated_at DESC")],
    )

    op.create_table(
        "news_admin_actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("action_kind", sa.Text(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("performed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("performed_by_label", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_news_admin_actions_kind_performed_at",
        "news_admin_actions",
        ["action_kind", sa.text("performed_at DESC")],
    )


def _add_article_extraction_foreign_keys() -> None:
    op.create_foreign_key(
        "fk_news_articles_triage_extraction_id_news_extractions",
        "news_articles",
        "news_extractions",
        ["triage_extraction_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_news_articles_current_extraction_id_news_extractions",
        "news_articles",
        "news_extractions",
        ["current_extraction_id"],
        ["id"],
        ondelete="SET NULL",
    )


def _create_summary_views() -> None:
    op.execute(
        """
        CREATE VIEW news_articles_summary
        WITH (security_invoker = false) AS
        SELECT
            id,
            news_source_id,
            url_canonical,
            fetch_status,
            fetched_at,
            http_status,
            title,
            byline_author,
            published_at,
            publication_section,
            tags,
            language,
            paywall_state,
            triage_status,
            triage_at,
            current_extraction_id,
            current_extraction_version,
            ingest_method,
            ingested_by_user_id,
            created_at,
            updated_at
        FROM news_articles
        """
    )
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
    op.execute(
        """
        CREATE VIEW news_project_references_summary
        WITH (security_invoker = false) AS
        SELECT
            id,
            extraction_id,
            article_id,
            reference_index,
            candidate_name,
            candidate_address,
            candidate_developer,
            candidate_unit_total,
            candidate_unit_affordable,
            candidate_unit_market_rate,
            candidate_product_type,
            candidate_age_restriction,
            candidate_status_signal,
            candidate_delivery_year_text,
            candidate_delivery_year_normalized,
            candidate_signal_flags,
            candidate_identifiers,
            candidate_neighborhood,
            candidate_lat,
            candidate_lng,
            candidate_confidence,
            match_status,
            matched_project_id,
            match_confidence,
            match_reason,
            match_candidates,
            match_decision_at,
            matched_evidence_id,
            review_item_id,
            manual_relink_by_user_id,
            manual_relink_at,
            manual_relink_note,
            created_at,
            updated_at
        FROM news_project_references
        """
    )


def _seed_sentinel_scope() -> None:
    op.execute(
        f"""
        INSERT INTO markets (
            id,
            slug,
            name,
            display_name,
            state,
            market_type
        )
        VALUES (
            '{UNSCOPED_MARKET_ID}',
            'unscoped',
            'Unscoped',
            'Unscoped',
            'NA',
            'sentinel'
        )
        ON CONFLICT (slug) DO UPDATE
        SET
            name = EXCLUDED.name,
            display_name = EXCLUDED.display_name,
            state = EXCLUDED.state,
            market_type = EXCLUDED.market_type,
            updated_at = now()
        """
    )
    op.execute(
        f"""
        INSERT INTO jurisdictions (
            id,
            slug,
            name,
            display_name,
            state,
            market_id,
            entity_type
        )
        VALUES (
            '{UNSCOPED_JURISDICTION_ID}',
            'unknown_unscoped',
            'Unknown / Unscoped',
            'Unknown / Unscoped',
            'NA',
            (SELECT id FROM markets WHERE slug = 'unscoped' LIMIT 1),
            'sentinel'
        )
        ON CONFLICT (state, slug) DO UPDATE
        SET
            name = EXCLUDED.name,
            display_name = EXCLUDED.display_name,
            market_id = EXCLUDED.market_id,
            entity_type = EXCLUDED.entity_type,
            updated_at = now()
        """
    )


def _seed_initial_rows() -> None:
    _seed_sentinel_scope()
    op.execute(
        """
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
            '66f2fb8f-b861-57be-9856-5c56ec208d07',
            'bizjournals_la',
            'L.A. Business Journal',
            'https://www.bizjournals.com/losangeles',
            'BizJournalsCollector',
            true,
            '0 13 * * *',
            'America/Los_Angeles',
            '{}'::jsonb,
            (SELECT id FROM markets WHERE slug = 'los_angeles' LIMIT 1),
            (
                SELECT id
                FROM jurisdictions
                WHERE slug = 'city_of_los_angeles'
                  AND state = 'CA'
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
    op.execute(
        """
        INSERT INTO news_cost_caps (effective_date, daily_warn_usd, daily_hard_usd)
        VALUES (CURRENT_DATE, 25.00, 35.00)
        ON CONFLICT (effective_date) DO NOTHING
        """
    )

    signal_flags_table = sa.table(
        "news_signal_flag_registry",
        sa.column("flag_key", sa.Text()),
        sa.column("display_label", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("example_phrases", postgresql.ARRAY(sa.Text())),
    )
    op.bulk_insert(
        signal_flags_table,
        [
            {
                "flag_key": flag_key,
                "display_label": display_label,
                "category": category,
                "description": description,
                "example_phrases": example_phrases,
            }
            for flag_key, display_label, category, description, example_phrases in SIGNAL_FLAGS
        ],
    )


def _configure_rls_and_grants() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO authenticated")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {NEWS_SUMMARY_READER_ROLE}")

    for table_name in AUTHENTICATED_READ_TABLES:
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

    for table_name in SUMMARY_VIEW_BASE_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT ON TABLE {table_name} FROM authenticated")

    for table_name in PRIVATE_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")

    op.execute("GRANT SELECT ON TABLE news_articles_summary TO authenticated")
    op.execute("GRANT SELECT ON TABLE news_extractions_summary TO authenticated")
    op.execute("GRANT SELECT ON TABLE news_project_references_summary TO authenticated")


def _drop_rls_and_grants() -> None:
    op.execute("REVOKE SELECT ON TABLE news_project_references_summary FROM authenticated")
    op.execute("REVOKE SELECT ON TABLE news_extractions_summary FROM authenticated")
    op.execute("REVOKE SELECT ON TABLE news_articles_summary FROM authenticated")

    for table_name in PRIVATE_TABLES:
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    for table_name in reversed(SUMMARY_VIEW_BASE_TABLES):
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    for table_name in AUTHENTICATED_READ_TABLES:
        op.execute(f"DROP POLICY IF EXISTS authenticated_read_{table_name} ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT ON TABLE {table_name} FROM authenticated")
