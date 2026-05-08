from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


def test_news_research_seed_rows_are_present(postgres_session: Session) -> None:
    _ensure_news_schema(postgres_session)

    news_source = postgres_session.execute(
        text(
            """
            SELECT
                slug,
                name,
                active,
                schedule_cron,
                schedule_timezone,
                market_id,
                jurisdiction_id
            FROM news_sources
            WHERE slug = 'bizjournals_la'
            """
        )
    ).mappings().one_or_none()
    assert news_source is not None
    assert news_source["name"] == "L.A. Business Journal"
    assert news_source["active"] is False
    assert news_source["schedule_cron"] is None
    assert news_source["schedule_timezone"] == "America/Los_Angeles"
    assert news_source["market_id"] is not None
    assert news_source["jurisdiction_id"] is not None

    urbanize_source = postgres_session.execute(
        text(
            """
            SELECT
                slug,
                name,
                active,
                schedule_cron,
                schedule_timezone,
                market_id,
                jurisdiction_id,
                config
            FROM news_sources
            WHERE slug = 'urbanize_la'
            """
        )
    ).mappings().one_or_none()
    assert urbanize_source is not None
    assert urbanize_source["name"] == "Urbanize LA"
    assert urbanize_source["active"] is True
    assert urbanize_source["schedule_cron"] == "30 7 * * *"
    assert urbanize_source["schedule_timezone"] == "America/Los_Angeles"
    assert urbanize_source["market_id"] is None
    assert urbanize_source["jurisdiction_id"] is None
    assert urbanize_source["config"]["fetch_path"] == "polite"
    assert urbanize_source["config"]["hosts"] == ["la.urbanize.city"]
    assert urbanize_source["config"]["backfill_window_days"] == 56
    assert urbanize_source["config"]["source_strategy_doc"] == (
        "docs/sources/news/urbanize_la.md"
    )

    paste_source = postgres_session.execute(
        text(
            """
            SELECT news_sources.slug, jurisdictions.slug AS jurisdiction_slug
            FROM news_sources
            JOIN jurisdictions ON jurisdictions.id = news_sources.jurisdiction_id
            WHERE news_sources.slug = 'news_paste_a_link'
            """
        )
    ).mappings().one_or_none()
    assert paste_source is not None
    assert paste_source["jurisdiction_slug"] == "unknown_unscoped"

    sentinel = postgres_session.execute(
        text(
            """
            SELECT jurisdictions.name, markets.slug AS market_slug
            FROM jurisdictions
            JOIN markets ON markets.id = jurisdictions.market_id
            WHERE jurisdictions.state = 'NA'
              AND jurisdictions.slug = 'unknown_unscoped'
            """
        )
    ).mappings().one_or_none()
    assert sentinel is not None
    assert sentinel["name"] == "Unknown / Unscoped"
    assert sentinel["market_slug"] == "unscoped"

    cost_cap = postgres_session.execute(
        text(
            """
            SELECT effective_from, daily_warn_usd, daily_hard_usd
            FROM cost_caps
            WHERE bucket = 'news'
              AND effective_from <= CURRENT_DATE
              AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
            ORDER BY effective_from DESC
            LIMIT 1
            """
        )
    ).mappings().one_or_none()
    assert cost_cap is not None
    assert cost_cap["daily_warn_usd"] >= Decimal("25.00")
    assert cost_cap["daily_hard_usd"] >= Decimal("35.00")
    assert cost_cap["daily_warn_usd"] <= cost_cap["daily_hard_usd"]

    flag_summary = postgres_session.execute(
        text(
            """
            SELECT category, count(*) AS flag_count
            FROM news_signal_flag_registry
            GROUP BY category
            """
        )
    ).mappings().all()
    assert {row["category"]: row["flag_count"] for row in flag_summary} == {
        "meta": 5,
        "milestone": 7,
        "project_change": 6,
        "risk": 7,
    }


def test_news_summary_views_hide_raw_content_and_use_reader_role(
    postgres_session: Session,
) -> None:
    _ensure_news_schema(postgres_session)
    _ensure_authenticated_role(postgres_session)

    assert _role_exists(postgres_session, "news_summary_reader")
    assert not _view_is_security_invoker(postgres_session, "news_articles_summary")
    assert not _view_is_security_invoker(postgres_session, "news_extractions_summary")
    assert not _view_is_security_invoker(
        postgres_session,
        "news_project_references_summary",
    )

    article_columns = _relation_columns(postgres_session, "news_articles_summary")
    assert "raw_html" not in article_columns
    assert "body_text" not in article_columns

    extraction_columns = _relation_columns(postgres_session, "news_extractions_summary")
    assert "output_json" not in extraction_columns
    assert "raw_response_text" not in extraction_columns
    assert "input_tokens_cache_creation" in extraction_columns

    reference_columns = _relation_columns(postgres_session, "news_project_references_summary")
    assert "candidate_city" in reference_columns
    assert "passage_excerpts" not in reference_columns

    assert _has_select(postgres_session, "authenticated", "news_articles_summary")
    assert _has_select(postgres_session, "authenticated", "news_extractions_summary")
    assert _has_select(postgres_session, "authenticated", "news_project_references_summary")
    assert not _has_select(postgres_session, "authenticated", "news_articles")
    assert not _has_select(postgres_session, "authenticated", "news_extractions")
    assert not _has_select(postgres_session, "authenticated", "news_project_references")


def test_news_scrape_active_dedup_index_is_source_scoped(
    postgres_session: Session,
) -> None:
    _ensure_news_schema(postgres_session)
    indexdef = postgres_session.execute(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'scrape_jobs'
              AND indexname = 'uq_scrape_jobs_one_active_news_scrape'
            """
        )
    ).scalar_one_or_none()

    assert indexdef is not None
    assert "(source_name)" in indexdef
    assert "kind)::text = 'news_scrape'" in indexdef
    assert "COALESCE" not in indexdef


def test_agent1_foundation_schema_is_present(postgres_session: Session) -> None:
    _ensure_news_schema(postgres_session)

    assert _to_regclass(postgres_session, "cost_cap_overrides") is not None
    assert _to_regclass(postgres_session, "news_article_chunks") is not None
    assert _to_regclass(postgres_session, "news_reference_auto_applied") is not None

    llm_cost_usage_columns = _relation_columns(postgres_session, "llm_cost_usage")
    assert {
        "bucket",
        "cost_date",
        "capability",
        "provider",
        "model",
        "call_count",
        "spent_usd",
    }.issubset(llm_cost_usage_columns)

    chunk_embedding_type = postgres_session.execute(
        text(
            """
            SELECT udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'news_article_chunks'
              AND column_name = 'embedding'
            """
        )
    ).scalar_one()
    assert chunk_embedding_type == "vector"

    project_location_index = postgres_session.execute(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'projects'
              AND indexname = 'ix_projects_location_gist'
            """
        )
    ).scalar_one_or_none()
    assert project_location_index is not None
    assert "using gist" in project_location_index.lower()

    nullable_source_run_index = postgres_session.execute(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'news_reference_auto_applied'
              AND indexname = 'uq_news_reference_auto_applied_null_source_run_reference_gate'
            """
        )
    ).scalar_one_or_none()
    assert nullable_source_run_index is not None
    assert "unique" in nullable_source_run_index.lower()
    assert "(article_id, reference_index, gate)" in nullable_source_run_index
    assert "source_run_id IS NULL" in nullable_source_run_index


def _ensure_news_schema(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    if not inspector.has_table("news_sources"):
        pytest.skip("Apply migration 202604290019 before running news schema tests.")
    if not inspector.has_table("scrape_jobs"):
        pytest.skip("Apply migration 202604290020 before running news schema tests.")
    news_source_columns = {
        column["name"] for column in inspector.get_columns("news_sources")
    }
    if "schedule_timezone" not in news_source_columns:
        pytest.skip("Apply the latest D.1 news schema migration before running tests.")
    urbanize_source = postgres_session.execute(
        text("SELECT 1 FROM news_sources WHERE slug = 'urbanize_la'")
    ).scalar_one_or_none()
    if urbanize_source is None:
        pytest.skip("Apply migration 202605010025 before running news schema tests.")
    extraction_columns = {
        column["name"] for column in inspector.get_columns("news_extractions")
    }
    agent1_tables = (
        "cost_caps",
        "cost_cap_overrides",
        "llm_cost_usage",
        "news_article_chunks",
        "news_reference_auto_applied",
    )
    missing_agent1_tables = [
        table_name for table_name in agent1_tables if not inspector.has_table(table_name)
    ]
    if missing_agent1_tables:
        pytest.skip("Apply migration 202605040028 before running news schema tests.")
    cost_columns = {column["name"] for column in inspector.get_columns("llm_cost_usage")}
    if "input_tokens_cache_creation" not in extraction_columns:
        pytest.skip("Apply migration 202604290022 before running news schema tests.")
    if "input_tokens_cache_creation" not in cost_columns:
        pytest.skip("Apply migration 202605040028 before running news schema tests.")
    reference_columns = {
        column["name"] for column in inspector.get_columns("news_project_references")
    }
    if "candidate_city" not in reference_columns:
        pytest.skip("Apply migration 202605080035 before running news schema tests.")
    missing_views = [
        view_name
        for view_name in (
            "news_articles_summary",
            "news_extractions_summary",
            "news_project_references_summary",
        )
        if _to_regclass(postgres_session, view_name) is None
    ]
    if missing_views:
        pytest.skip(f"Apply the latest D.1 news schema migration: {missing_views}")


def _ensure_authenticated_role(postgres_session: Session) -> None:
    role_exists = postgres_session.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')")
    ).scalar_one()
    if not role_exists:
        pytest.skip("The authenticated Postgres role is required for grant tests.")


def _to_regclass(postgres_session: Session, relation_name: str) -> str | None:
    return postgres_session.execute(
        text("SELECT to_regclass(:relation_name)"),
        {"relation_name": f"public.{relation_name}"},
    ).scalar_one()


def _role_exists(postgres_session: Session, role_name: str) -> bool:
    return postgres_session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = :role_name
            )
            """
        ),
        {"role_name": role_name},
    ).scalar_one()


def _view_is_security_invoker(postgres_session: Session, view_name: str) -> bool:
    return postgres_session.execute(
        text(
            """
            SELECT COALESCE(
                (
                    SELECT option_value::boolean
                    FROM pg_options_to_table(pg_class.reloptions)
                    WHERE option_name = 'security_invoker'
                ),
                false
            )
            FROM pg_class
            WHERE oid = to_regclass(:view_name)
            """
        ),
        {"view_name": f"public.{view_name}"},
    ).scalar_one()


def _relation_columns(postgres_session: Session, relation_name: str) -> set[str]:
    rows = postgres_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :relation_name
            """
        ),
        {"relation_name": relation_name},
    ).scalars()
    return set(rows)


def _has_select(postgres_session: Session, role_name: str, relation_name: str) -> bool:
    return postgres_session.execute(
        text("SELECT has_table_privilege(:role_name, :relation_name, 'SELECT')"),
        {"role_name": role_name, "relation_name": f"public.{relation_name}"},
    ).scalar_one()
