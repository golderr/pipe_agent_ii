from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    CostCap,
    LLMCostUsage,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsSource,
    NewsTriageStatus,
    SystemAlert,
)
from tcg_pipeline.news.costs import reserve_llm_cost
from tcg_pipeline.news.prompts import load_prompt, render_triage_prompt
from tcg_pipeline.news.triage import (
    LLMUsage,
    TriageLLMResponse,
    calculate_llm_cost_usd,
    parse_triage_response,
    persist_triage_response,
    run_news_triage_for_article,
)
from tcg_pipeline.settings import Settings


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


def test_parse_triage_response_does_not_trust_truncated_or_refused_json() -> None:
    truncated = parse_triage_response(
        '{"relevant": true, "reason": "Looks relevant."}',
        stop_reason="max_tokens",
    )
    refused = parse_triage_response(
        '{"relevant": true, "reason": "Looks relevant."}',
        stop_reason="refusal",
    )

    assert truncated.decision is None
    assert truncated.parse_status == NewsExtractionParseStatus.TRUNCATED.value
    assert refused.decision is None
    assert refused.parse_status == NewsExtractionParseStatus.REFUSED.value


def test_prompt_loader_rejects_prompt_ids_without_version_suffix() -> None:
    with pytest.raises(RuntimeError, match="Expected convention"):
        load_prompt("triage")


def test_llm_cost_calculation_prices_cache_creation_separately() -> None:
    cost = calculate_llm_cost_usd(
        "claude-haiku-4-5-20251001",
        input_tokens_uncached=1000,
        input_tokens_cache_creation=1000,
        input_tokens_cached=1000,
        output_tokens=100,
    )

    assert cost == Decimal("0.002850")
    with pytest.raises(RuntimeError, match="Unknown news LLM model pricing"):
        calculate_llm_cost_usd(
            "claude-unknown",
            input_tokens_uncached=1,
            input_tokens_cache_creation=0,
            input_tokens_cached=0,
            output_tokens=0,
        )


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
            input_tokens_cache_creation=25,
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
    assert extraction.model_provider == "anthropic"
    assert extraction.parse_status == NewsExtractionParseStatus.OK.value
    assert extraction.output_json["relevant"] is True
    cost = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == NewsExtractionPass.TRIAGE.value,
            LLMCostUsage.provider == "anthropic",
            LLMCostUsage.model == "claude-haiku-4-5-20251001",
        )
    ).scalar_one()
    assert cost.call_count == 1
    assert cost.input_tokens_uncached == 1000
    assert cost.input_tokens_cache_creation == 25
    assert cost.input_tokens_cached == 100
    assert cost.output_tokens == 50
    assert Decimal(cost.spent_usd) == Decimal("0.001291")


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
                    input_tokens_cache_creation=5,
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
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == "reserved",
            LLMCostUsage.provider == "_reservation_",
            LLMCostUsage.model == "_reservation_",
        )
    ).scalar_one()
    assert Decimal(reservation.spent_usd) == Decimal("0.000000")


def test_run_news_triage_for_article_skips_and_alerts_without_api_key(
    postgres_session: Session,
) -> None:
    _ensure_news_triage_tables(postgres_session)
    source = _news_source(postgres_session)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/no-key-{uuid.uuid4().hex}",
        url_original="https://example.com/no-key",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        body_text="A developer filed plans for apartments.",
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

    result = run_news_triage_for_article(
        article.id,
        settings=Settings(app_env="test", anthropic_api_key=None),
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert result.skipped_reason == "no_api_key"
    assert result.triage_status == NewsTriageStatus.PENDING.value
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.triage_status == NewsTriageStatus.PENDING.value
    alert = postgres_session.execute(
        select(SystemAlert).where(
            SystemAlert.alert_key == "news_anthropic_api_key_missing"
        )
    ).scalar_one()
    assert alert.severity == "warning"
    assert alert.scope == {"component": "news_triage"}


def test_reserve_llm_cost_hard_cap_creates_alert(postgres_session: Session) -> None:
    _ensure_news_triage_tables(postgres_session)
    cost_date = date(2099, 1, 1)
    postgres_session.add(
        CostCap(
            bucket="news",
            effective_from=cost_date,
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
        "llm_cost_usage",
        "cost_caps",
        "system_alerts",
    }
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running triage tests: {missing}")
    extraction_columns = {
        column["name"] for column in inspector.get_columns("news_extractions")
    }
    cost_columns = {column["name"] for column in inspector.get_columns("llm_cost_usage")}
    if "input_tokens_cache_creation" not in extraction_columns:
        pytest.skip("Apply migration 202604290022 before running triage tests.")
    if "input_tokens_cache_creation" not in cost_columns:
        pytest.skip("Apply migration 202605040028 before running triage tests.")


def _news_source(postgres_session: Session) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "news_paste_a_link")
    ).scalar_one_or_none()
    if source is None:
        pytest.skip("Apply migration 202604290021 before running triage tests.")
    return source
