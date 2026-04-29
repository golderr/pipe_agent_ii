from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    NewsArticle,
    NewsCostCap,
    NewsExtraction,
    NewsExtractionCost,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsSource,
    NewsTriageStatus,
    SystemAlert,
)
from tcg_pipeline.news.costs import reserve_llm_cost
from tcg_pipeline.news.prompts import render_triage_prompt
from tcg_pipeline.news.triage import (
    LLMUsage,
    TriageLLMResponse,
    parse_triage_response,
    persist_triage_response,
    run_news_triage_for_article,
)


def test_parse_triage_response_overrides_uncertain_negative() -> None:
    parsed = parse_triage_response(
        '{"relevant": false, "reason": "Unclear, but possibly about a development site."}'
    )

    assert parsed.parse_status == NewsExtractionParseStatus.OK.value
    assert parsed.decision is not None
    assert parsed.decision.original_relevant is False
    assert parsed.decision.relevant is True
    assert parsed.decision.overridden_to_relevant is True
    assert parsed.output_json == {
        "relevant": True,
        "reason": "Unclear, but possibly about a development site.",
        "original_relevant": False,
        "overridden_to_relevant": True,
    }


def test_parse_triage_response_rejects_schema_drift() -> None:
    parsed = parse_triage_response('{"relevant": "yes", "reason": "", "extra": true}')

    assert parsed.decision is None
    assert parsed.parse_status == NewsExtractionParseStatus.SCHEMA_INVALID.value
    assert parsed.parse_error_text is not None


def test_persist_triage_response_writes_extraction_article_status_and_cost(
    postgres_session: Session,
) -> None:
    _ensure_news_triage_tables(postgres_session)
    source = _news_source(postgres_session)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/triage-{uuid.uuid4().hex}",
        url_original="https://example.com/triage",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        body_text="Developer announced a 120-unit apartment project in Los Angeles.",
        body_text_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        structural_signals={
            "extractor_version": "v1",
            "ran_at": "2026-04-29T12:00:00+00:00",
            "signals": [
                {
                    "extractor": "unit_count",
                    "raw_match": "120-unit",
                    "offset_start": 23,
                    "offset_end": 31,
                    "canonical": 120,
                    "confidence": 0.95,
                    "metadata": {},
                }
            ],
        },
        title="Developer announces project",
        ingest_method="news_paste_a_link",
    )
    postgres_session.add(article)
    postgres_session.flush()
    rendered_prompt = render_triage_prompt(article)
    llm_response = TriageLLMResponse(
        text='{"relevant": true, "reason": "The article announces a project."}',
        model="claude-haiku-4-5-20251001",
        usage=LLMUsage(
            input_tokens_uncached=1000,
            input_tokens_cached=100,
            output_tokens=50,
        ),
        latency_ms=123,
        stop_reason="end_turn",
    )

    result = persist_triage_response(
        postgres_session,
        article_id=article.id,
        rendered_prompt=rendered_prompt,
        llm_response=llm_response,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )
    postgres_session.flush()

    assert result.triage_status == NewsTriageStatus.RELEVANT.value
    assert result.relevant is True
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.triage_status == NewsTriageStatus.RELEVANT.value
    assert refreshed_article.triage_extraction_id == result.extraction_id
    extraction = postgres_session.get(NewsExtraction, result.extraction_id)
    assert extraction is not None
    assert extraction.pass_name == NewsExtractionPass.TRIAGE.value
    assert extraction.prompt_id == "triage_v1"
    assert extraction.parse_status == NewsExtractionParseStatus.OK.value
    assert extraction.output_json["relevant"] is True
    cost = postgres_session.execute(
        select(NewsExtractionCost).where(
            NewsExtractionCost.cost_date == date(2026, 4, 29),
            NewsExtractionCost.pass_name == NewsExtractionPass.TRIAGE.value,
            NewsExtractionCost.model == "claude-haiku-4-5-20251001",
        )
    ).scalar_one()
    assert cost.call_count == 1
    assert cost.input_tokens_uncached == 1000
    assert cost.input_tokens_cached == 100
    assert cost.output_tokens == 50
    assert Decimal(cost.cost_usd) == Decimal("0.001260")


def test_run_news_triage_for_article_reserves_calls_client_and_true_ups(
    postgres_session: Session,
) -> None:
    _ensure_news_triage_tables(postgres_session)
    source = _news_source(postgres_session)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/triage-run-{uuid.uuid4().hex}",
        url_original="https://example.com/triage-run",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        body_text="A developer filed plans for an 80-unit apartment project.",
        body_text_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        structural_signals={
            "extractor_version": "v1",
            "ran_at": "2026-04-29T12:00:00+00:00",
            "signals": [],
        },
        title="Plans filed",
        ingest_method="news_paste_a_link",
    )
    postgres_session.add(article)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    class FakeTriageClient:
        model = "claude-haiku-4-5-20251001"

        def triage(self, prompt):  # type: ignore[no-untyped-def]
            assert "80-unit apartment project" in prompt.user_text
            return TriageLLMResponse(
                text='{"relevant": true, "reason": "It describes a planned project."}',
                model=self.model,
                usage=LLMUsage(
                    input_tokens_uncached=100,
                    input_tokens_cached=0,
                    output_tokens=10,
                ),
                latency_ms=20,
                stop_reason="end_turn",
            )

    result = run_news_triage_for_article(
        article.id,
        client=FakeTriageClient(),
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert result.triage_status == NewsTriageStatus.RELEVANT.value
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.triage_extraction_id == result.extraction_id
    reservation = postgres_session.execute(
        select(NewsExtractionCost).where(
            NewsExtractionCost.cost_date == date(2026, 4, 29),
            NewsExtractionCost.pass_name == "reserved",
            NewsExtractionCost.model == "_reservation_",
        )
    ).scalar_one()
    assert Decimal(reservation.cost_usd) == Decimal("0.000000")


def test_reserve_llm_cost_hard_cap_creates_alert(postgres_session: Session) -> None:
    _ensure_news_triage_tables(postgres_session)
    cost_date = date(2099, 1, 1)
    postgres_session.add(
        NewsCostCap(
            effective_date=cost_date,
            daily_warn_usd=Decimal("0.01"),
            daily_hard_usd=Decimal("0.02"),
        )
    )
    postgres_session.flush()

    reservation = reserve_llm_cost(
        postgres_session,
        pass_name=NewsExtractionPass.TRIAGE.value,
        model="claude-haiku-4-5-20251001",
        estimated_cost_usd=Decimal("0.03"),
        now=datetime(2099, 1, 1, 12, 0, tzinfo=UTC),
    )
    postgres_session.flush()

    assert reservation is None
    alert = postgres_session.execute(
        select(SystemAlert).where(
            SystemAlert.alert_key == "news_daily_cost_hard_cap_reached"
        )
    ).scalar_one()
    assert alert.severity == "high"
    assert alert.scope == {"cost_date": "2099-01-01"}


def _ensure_news_triage_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "news_articles",
        "news_extractions",
        "news_extraction_costs",
        "news_cost_caps",
        "system_alerts",
    }
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running triage tests: {missing}")


def _news_source(postgres_session: Session) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "news_paste_a_link")
    ).scalar_one_or_none()
    if source is None:
        pytest.skip("Apply migration 202604290021 before running triage tests.")
    return source
