from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tcg_pipeline.cli import app
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    Evidence,
    NewsArticle,
    NewsExtraction,
    NewsProjectReference,
    NewsSource,
    ReviewItem,
    SourceRun,
)
from tcg_pipeline.news import ab_harness
from tcg_pipeline.news.extraction import ExtractionLLMResponse
from tcg_pipeline.news.llm import LLMUsage
from tcg_pipeline.settings import get_settings

runner = CliRunner()
CANNED_EXTRACTION_PAYLOAD = {
    "relevance": "confirmed",
    "rejected_reason": None,
    "project_references": [
        {
            "candidate_name": "Test Tower",
            "candidate_address": "123 Main Street",
            "candidate_city": "Los Angeles",
            "candidate_developer": "Acme Development",
            "candidate_unit_total": 42,
            "candidate_unit_affordable": None,
            "candidate_unit_market_rate": None,
            "candidate_unit_workforce": None,
            "candidate_product_type": "apartment",
            "candidate_age_restriction": "non_age_restricted",
            "candidate_status_signal": "Proposed",
            "candidate_delivery_year_text": None,
            "candidate_delivery_year_normalized": None,
            "candidate_signal_flags": {},
            "candidate_identifiers": {
                "case_number": [],
                "permit_number": [],
                "apn": [],
            },
            "candidate_neighborhood": "Downtown Los Angeles",
            "candidate_lat": None,
            "candidate_lng": None,
            "candidate_confidence": "medium",
            "passage_excerpts": [
                {
                    "field": "candidate_name",
                    "value": "Test Tower",
                    "passage": "Acme Development proposes Test Tower at 123 Main Street.",
                    "offset_start": 0,
                    "offset_end": 60,
                }
            ],
            "registry_developer_id": None,
            "registry_project_id": None,
        }
    ],
    "diagnostic": {},
}


def test_parse_candidate_specs_normalizes_provider_and_checks_pricing() -> None:
    candidates = ab_harness.parse_candidate_specs(
        "anthropic:claude-opus-4-7, vercel:anthropic/claude-sonnet-4-6, openai:gpt-5.4"
    )

    assert [candidate.key for candidate in candidates] == [
        "anthropic:claude-opus-4-7",
        "vercel_ai_gateway:anthropic/claude-sonnet-4-6",
        "openai:gpt-5.4",
    ]


def test_parse_candidate_specs_rejects_unknown_pricing() -> None:
    with pytest.raises(RuntimeError, match="Unknown news LLM model pricing"):
        ab_harness.parse_candidate_specs("openai:not-priced-yet")


def test_load_article_fixtures_validates_required_fields(tmp_path: Path) -> None:
    fixture_path = tmp_path / "articles.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "slug": "sample",
                    "url": "https://example.com/a",
                    "title": "Sample",
                    "body_text": "A sample article.",
                    "published_at": "2026-05-05T12:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    fixtures = ab_harness.load_article_fixtures(fixture_path)

    assert len(fixtures) == 1
    assert fixtures[0].slug == "sample"
    assert fixtures[0].published_at is not None
    assert fixtures[0].published_at.tzinfo is not None


def test_summarize_candidate_results_counts_pipeline_metrics() -> None:
    summaries = ab_harness.summarize_candidate_results(
        {
            "anthropic:claude-opus-4-7": [
                {
                    "parse_status": "ok",
                    "reference_count": 2,
                    "matcher_status_counts": {"confirmed": 1, "new_candidate": 1},
                    "match_type_counts": {"address_composite": 1, "new_candidate": 1},
                    "review_item_counts": {
                        "possible_match": 0,
                        "new_candidate": 1,
                        "status_change": 1,
                        "total_projected": 2,
                    },
                    "agent_trigger": {"would_trigger": True},
                    "cost_usd": "0.125000",
                    "latency_ms": 1000,
                },
                {
                    "parse_status": "schema_invalid",
                    "reference_count": 0,
                    "matcher_status_counts": {},
                    "match_type_counts": {},
                    "review_item_counts": {
                        "possible_match": 0,
                        "new_candidate": 0,
                        "status_change": 0,
                        "total_projected": 0,
                    },
                    "agent_trigger": {"would_trigger": False},
                    "cost_usd": "0.025000",
                    "latency_ms": 500,
                },
            ]
        }
    )

    assert summaries == [
        {
            "candidate": "anthropic:claude-opus-4-7",
            "articles": 2,
            "parse_status_counts": {"ok": 1, "schema_invalid": 1},
            "references": 2,
            "matcher_status_counts": {"confirmed": 1, "new_candidate": 1},
            "match_type_counts": {"address_composite": 1, "new_candidate": 1},
            "agent_trigger_articles": 1,
            "agent_trigger_rate": 0.5,
            "review_item_counts": {
                "new_candidate": 1,
                "possible_match": 0,
                "status_change": 1,
                "total_projected": 2,
            },
            "total_cost_usd": "0.150000",
            "avg_cost_usd": "0.075000",
            "avg_latency_ms": 750.0,
            "errors": 0,
        }
    ]


def test_preflight_candidate_clients_records_custom_client_success() -> None:
    candidate = ab_harness.ABExtractionCandidate(
        provider="anthropic",
        model="claude-opus-4-7",
    )
    client = _StubExtractionClient()

    results = ab_harness.preflight_candidate_clients({candidate.key: client}, (candidate,))

    assert client.preflight_calls == 1
    assert results[0]["candidate"] == "anthropic:claude-opus-4-7"
    assert results[0]["status"] == "ok"


def test_run_extraction_ab_harness_rolls_back_projection_writes(
    postgres_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_ab_harness_tables(postgres_session)
    source = _urbanize_source(postgres_session)
    fixture_path = tmp_path / "articles.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "slug": f"rollback-{uuid.uuid4().hex}",
                    "url": "https://la.urbanize.city/post/rollback-harness-test",
                    "title": "Test Tower proposed at 123 Main Street",
                    "body_text": (
                        "Acme Development proposes Test Tower at 123 Main Street "
                        "with 42 apartments in Downtown Los Angeles."
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )
    before_counts = _row_counts(postgres_session)
    monkeypatch.setattr(
        ab_harness,
        "_build_candidate_client",
        lambda _settings, _candidate: _StubExtractionClient(),
    )
    settings = get_settings().model_copy(update={"anthropic_api_key": "test-key"})

    report = ab_harness.run_extraction_ab_harness(
        fixture_path=fixture_path,
        candidates="anthropic:claude-opus-4-7",
        source_slug=source.slug,
        output_path=tmp_path / "report.json",
        settings=settings,
        session_factory=get_session_factory(),
        run_preflight=False,
    )

    postgres_session.expire_all()
    assert _row_counts(postgres_session) == before_counts
    assert report["preflight_results"][0]["status"] == "skipped"
    assert report["cost_accounting"]["llm_cost_usage_written"] is False
    assert report["candidate_summaries"][0]["articles"] == 1
    article_result = report["article_results"]["anthropic:claude-opus-4-7"][0]
    reference_result = article_result["reference_results"][0]
    assert article_result["diagnostic"] == {}
    assert reference_result["candidate_product_type"] == "apartment"
    assert reference_result["candidate_age_restriction"] == "non_age_restricted"
    assert reference_result["candidate_status_signal"] == "Proposed"
    assert reference_result["candidate_neighborhood"] == "Downtown Los Angeles"
    assert reference_result["candidate_city"] == "Los Angeles"
    assert reference_result["passage_excerpts"][0]["field"] == "candidate_name"


def test_news_ab_extract_cli_invokes_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "articles.json"
    output_path = tmp_path / "report.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "slug": "sample",
                    "url": "https://example.com/a",
                    "title": "Sample",
                    "body_text": "Sample body.",
                }
            ]
        ),
        encoding="utf-8",
    )
    calls = {}

    def fake_run_extraction_ab_harness(**kwargs):  # type: ignore[no-untyped-def]
        calls.update(kwargs)
        return {
            "output_path": str(output_path),
            "candidate_summaries": [
                {
                    "candidate": "anthropic:claude-opus-4-7",
                    "articles": 1,
                    "parse_status_counts": {"ok": 1},
                    "references": 1,
                    "agent_trigger_rate": 0.0,
                    "total_cost_usd": "0.010000",
                }
            ],
        }

    monkeypatch.setattr(ab_harness, "run_extraction_ab_harness", fake_run_extraction_ab_harness)

    result = runner.invoke(
        app,
        [
            "news",
            "ab-extract",
            "--fixture",
            str(fixture_path),
            "--candidates",
            "anthropic:claude-opus-4-7",
            "--source-slug",
            "urbanize_la",
            "--output",
            str(output_path),
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["fixture_path"] == fixture_path
    assert calls["candidates"] == "anthropic:claude-opus-4-7"
    assert calls["source_slug"] == "urbanize_la"
    assert calls["output_path"] == output_path
    assert calls["limit"] == 1
    assert "Running A/B harness against" in result.output
    assert "planned LLM calls=1" in result.output
    assert f"Report: {output_path}" in result.output


class _StubExtractionClient:
    model = "claude-opus-4-7"
    provider = "anthropic"

    def __init__(self) -> None:
        self.preflight_calls = 0

    def preflight(self) -> None:
        self.preflight_calls += 1

    def extract(self, _prompt: Any) -> ExtractionLLMResponse:
        return ExtractionLLMResponse(
            payload=CANNED_EXTRACTION_PAYLOAD,
            text=json.dumps(CANNED_EXTRACTION_PAYLOAD, sort_keys=True),
            model=self.model,
            provider=self.provider,
            usage=LLMUsage(
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=0,
                output_tokens=40,
            ),
            latency_ms=25,
        )


def _ensure_ab_harness_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "news_sources",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_signal_flag_registry",
        "evidence",
        "review_items",
        "source_runs",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running A/B harness tests: {missing}")


def _urbanize_source(postgres_session: Session) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "urbanize_la")
    ).scalar_one_or_none()
    if source is None:
        pytest.skip("Apply the Urbanize LA news-source seed before running A/B harness tests.")
    return source


def _row_counts(postgres_session: Session) -> dict[str, int]:
    models = [
        NewsArticle,
        NewsExtraction,
        NewsProjectReference,
        Evidence,
        ReviewItem,
        SourceRun,
    ]
    return {
        model.__tablename__: postgres_session.scalar(select(func.count()).select_from(model)) or 0
        for model in models
    }
