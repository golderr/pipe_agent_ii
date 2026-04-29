from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsExtractionCost,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsProjectReference,
    NewsSignalFlag,
    NewsSource,
    NewsTriageStatus,
    SystemAlert,
)
from tcg_pipeline.news.extraction import (
    ExtractionLLMResponse,
    NewsExtractionRunResult,
    parse_extraction_response,
    persist_extraction_response,
    run_news_extraction_for_article,
)
from tcg_pipeline.news.llm import LLMUsage
from tcg_pipeline.news.prompts import render_extraction_prompt
from tcg_pipeline.settings import Settings


def test_parse_extraction_response_filters_unknown_signal_flags() -> None:
    parsed = parse_extraction_response(
        _payload(candidate_signal_flags={"groundbreaking_announced": True, "made_up": True}),
        raw_text="",
        active_signal_flags={"groundbreaking_announced"},
    )

    assert parsed.parse_status == NewsExtractionParseStatus.OK.value
    assert parsed.payload is not None
    reference = parsed.payload["project_references"][0]
    assert reference["candidate_signal_flags"] == {"groundbreaking_announced": True}
    assert parsed.unknown_signal_flags == ("made_up",)
    assert parsed.payload["diagnostic"]["unknown_signal_flags"] == ["made_up"]


def test_parse_extraction_response_rejects_schema_drift() -> None:
    payload = _payload()
    payload["project_references"][0]["candidate_status_signal"] = "Started"

    parsed = parse_extraction_response(
        payload,
        raw_text="",
        active_signal_flags={"groundbreaking_announced"},
    )

    assert parsed.parse_status == NewsExtractionParseStatus.SCHEMA_INVALID.value
    assert parsed.parse_error_text is not None


def test_parse_extraction_response_does_not_trust_truncated_or_refused_json() -> None:
    truncated = parse_extraction_response(
        _payload(),
        raw_text="",
        stop_reason="max_tokens",
    )
    refused = parse_extraction_response(
        _payload(),
        raw_text="",
        stop_reason="refusal",
    )

    assert truncated.payload is None
    assert truncated.parse_status == NewsExtractionParseStatus.TRUNCATED.value
    assert refused.payload is None
    assert refused.parse_status == NewsExtractionParseStatus.REFUSED.value


def test_persist_extraction_response_writes_extraction_references_article_pointer_and_cost(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    rendered_prompt = render_extraction_prompt(postgres_session, article)
    response = ExtractionLLMResponse(
        payload=_payload(),
        text="{}",
        model="claude-opus-4-7",
        usage=LLMUsage(
            input_tokens_uncached=1000,
            input_tokens_cache_creation=100,
            input_tokens_cached=200,
            output_tokens=50,
        ),
        latency_ms=1234,
        stop_reason="tool_use",
    )

    result = persist_extraction_response(
        postgres_session,
        article_id=article.id,
        rendered_prompt=rendered_prompt,
        llm_response=response,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )
    postgres_session.flush()

    assert isinstance(result, NewsExtractionRunResult)
    assert result.relevance == "confirmed"
    assert result.reference_count == 1
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    assert refreshed_article.current_extraction_version == 1
    extraction = postgres_session.get(NewsExtraction, result.extraction_id)
    assert extraction is not None
    assert extraction.pass_name == NewsExtractionPass.EXTRACTION.value
    assert extraction.prompt_id == "extract_v1"
    assert extraction.parse_status == NewsExtractionParseStatus.OK.value
    reference = postgres_session.execute(
        select(NewsProjectReference).where(
            NewsProjectReference.extraction_id == extraction.id
        )
    ).scalar_one()
    assert reference.candidate_name == "Helio"
    assert reference.candidate_unit_total == 140
    assert reference.candidate_signal_flags == {"groundbreaking_announced": True}
    assert reference.candidate_delivery_year_normalized == date(2027, 11, 1)
    assert reference.match_status == "pending"
    cost = postgres_session.execute(
        select(NewsExtractionCost).where(
            NewsExtractionCost.cost_date == date(2026, 4, 29),
            NewsExtractionCost.pass_name == NewsExtractionPass.EXTRACTION.value,
            NewsExtractionCost.model == "claude-opus-4-7",
        )
    ).scalar_one()
    assert cost.call_count == 1
    assert cost.input_tokens_uncached == 1000
    assert cost.input_tokens_cache_creation == 100
    assert cost.input_tokens_cached == 200
    assert cost.output_tokens == 50
    assert Decimal(cost.cost_usd) == Decimal("0.020925")


def test_run_news_extraction_for_article_reserves_calls_client_and_true_ups(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    class FakeExtractionClient:
        model = "claude-opus-4-7"

        def extract(self, prompt):  # type: ignore[no-untyped-def]
            assert "Helio" in prompt.user_text
            assert "Signal flag registry:" in prompt.system_text
            assert len(prompt.system_blocks) == 3
            return ExtractionLLMResponse(
                payload=_payload(),
                text="{}",
                model=self.model,
                usage=LLMUsage(
                    input_tokens_uncached=100,
                    input_tokens_cache_creation=0,
                    input_tokens_cached=10,
                    output_tokens=20,
                ),
                latency_ms=100,
                stop_reason="tool_use",
            )

    result = run_news_extraction_for_article(
        article.id,
        client=FakeExtractionClient(),
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert result.reference_count == 1
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    reservation = postgres_session.execute(
        select(NewsExtractionCost).where(
            NewsExtractionCost.cost_date == date(2026, 4, 29),
            NewsExtractionCost.pass_name == "reserved",
            NewsExtractionCost.model == "_reservation_",
        )
    ).scalar_one()
    assert Decimal(reservation.cost_usd) == Decimal("0.000000")


def test_run_news_extraction_for_article_skips_and_alerts_without_api_key(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    result = run_news_extraction_for_article(
        article.id,
        settings=Settings(app_env="test", anthropic_api_key=None),
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert result.skipped_reason == "no_api_key"
    assert result.extraction_id is None
    alert = postgres_session.execute(
        select(SystemAlert).where(
            SystemAlert.alert_key == "news_anthropic_api_key_missing",
            SystemAlert.scope == {"component": "news_extraction"},
        )
    ).scalar_one()
    assert alert.severity == "warning"


def _payload(
    *,
    candidate_signal_flags: dict[str, bool] | None = None,
) -> dict:
    return {
        "relevance": "confirmed",
        "rejected_reason": None,
        "project_references": [
            {
                "candidate_name": "Helio",
                "candidate_address": "1234 Sunset Boulevard",
                "candidate_developer": "Atlas Development",
                "candidate_unit_total": 140,
                "candidate_unit_affordable": 14,
                "candidate_unit_market_rate": 126,
                "candidate_product_type": "apartment",
                "candidate_age_restriction": "non_age_restricted",
                "candidate_status_signal": "Under Construction",
                "candidate_delivery_year_text": "late 2027",
                "candidate_delivery_year_normalized": "2027-11-01",
                "candidate_signal_flags": candidate_signal_flags
                or {"groundbreaking_announced": True},
                "candidate_identifiers": {
                    "case_number": ["CPC-2024-1234"],
                    "permit_number": [],
                    "apn": [],
                },
                "candidate_neighborhood": "Echo Park",
                "candidate_lat": None,
                "candidate_lng": None,
                "candidate_confidence": "high",
                "passage_excerpts": [
                    {
                        "field": "candidate_unit_total",
                        "value": 140,
                        "passage": "The developer broke ground on a 140-unit project.",
                        "offset_start": 34,
                        "offset_end": 42,
                    }
                ],
                "registry_developer_id": None,
                "registry_project_id": None,
            }
        ],
        "diagnostic": {
            "structural_disagreements": [],
            "uncertain_offsets": [],
            "model_notes": None,
        },
    }


def _article(source: NewsSource) -> NewsArticle:
    return NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/extract-{uuid.uuid4().hex}",
        url_original="https://example.com/extract",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        body_text=(
            "Atlas Development broke ground on Helio, a 140-unit apartment project "
            "at 1234 Sunset Boulevard. It is expected to deliver in late 2027."
        ),
        body_text_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        structural_signals={
            "extractor_version": "v1",
            "ran_at": "2026-04-29T12:00:00+00:00",
            "signals": [],
        },
        title="Developer breaks ground on Helio",
        published_at=datetime(2026, 4, 28, 20, 0, tzinfo=UTC),
        ingest_method="news_paste_a_link",
    )


def _ensure_news_extraction_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_extraction_costs",
        "news_cost_caps",
        "news_signal_flag_registry",
        "system_alerts",
    }
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running extraction tests: {missing}")


def _news_source(postgres_session: Session) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "news_paste_a_link")
    ).scalar_one_or_none()
    if source is None:
        pytest.skip("Apply migration 202604290021 before running extraction tests.")
    if not postgres_session.execute(
        select(NewsSignalFlag).where(
            NewsSignalFlag.flag_key == "groundbreaking_announced"
        )
    ).scalar_one_or_none():
        pytest.skip("Apply migration 202604290019 before running extraction tests.")
    return source
