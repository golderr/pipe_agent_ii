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
    NewsProjectReference,
    NewsSignalFlag,
    NewsSource,
    NewsTriageStatus,
    PipelineStatus,
    Project,
    SystemAlert,
)
from tcg_pipeline.news.extraction import (
    EXTRACTION_ESTIMATED_COST_USD,
    ExtractionLLMResponse,
    NewsExtractionRunResult,
    parse_extraction_response,
    persist_extraction_response,
    run_news_extraction_for_article,
)
from tcg_pipeline.news.extraction_legacy import decide_pass3a_reextraction
from tcg_pipeline.news.llm import LLMUsage
from tcg_pipeline.news.prompts import (
    load_prompt,
    render_extract_retry_prompt,
    render_extraction_prompt,
    render_news_glossary,
    render_reextraction_prompt,
)
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


def test_decide_pass3a_reextraction_detects_parse_failure_and_low_confidence(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    parse_failure = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=None,
        parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
        parse_error_text="missing field",
    )
    low_confidence = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(candidate_confidence="low"),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add_all([parse_failure, low_confidence])
    postgres_session.flush()

    parse_decision = decide_pass3a_reextraction(article, parse_failure)
    low_confidence_decision = decide_pass3a_reextraction(article, low_confidence)

    assert parse_decision is not None
    assert parse_decision.triggered_by == "pass2_parse_error"
    assert parse_decision.context["parse_error_text"] == "missing field"
    assert low_confidence_decision is not None
    assert low_confidence_decision.triggered_by == "pass2_low_confidence"
    assert low_confidence_decision.context["low_confidence"][0]["fields"]


def test_decide_pass3a_reextraction_detects_structural_conflict(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "310-unit",
                "offset_start": 36,
                "offset_end": 44,
                "canonical": 310,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
    postgres_session.add(article)
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(candidate_unit_total=250),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add(extraction)
    postgres_session.flush()

    decision = decide_pass3a_reextraction(article, extraction)

    assert decision is not None
    assert decision.triggered_by == "pass1_pass2_conflict"
    assert decision.context["conflicts"][0]["field"] == "total_units"
    assert decision.context["conflicts"][0]["structural_value"] == 310
    assert decision.context["conflicts"][0]["extracted_value"] == 250


def test_decide_pass3a_reextraction_keeps_workforce_split_distinct(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "affordable_split_phrase",
                "raw_match": "18 workforce units",
                "offset_start": 50,
                "offset_end": 68,
                "canonical": {"kind": "workforce", "count": 18},
                "confidence": 0.8,
                "metadata": {},
            }
        ],
    }
    postgres_session.add(article)
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(
            candidate_unit_workforce=12,
            passage_excerpts=[
                {
                    "field": "candidate_unit_workforce",
                    "value": 12,
                    "passage": "The project includes 12 workforce units.",
                    "offset_start": 45,
                    "offset_end": 75,
                }
            ],
        ),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add(extraction)
    postgres_session.flush()

    decision = decide_pass3a_reextraction(article, extraction)

    assert decision is not None
    conflict = decision.context["conflicts"][0]
    assert conflict["field"] == "workforce_units"
    assert conflict["structural_value"] == 18
    assert conflict["extracted_value"] == 12


def test_decide_pass3a_reextraction_ignores_equivalent_address_format(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "address",
                "raw_match": "501 E. 5th Street",
                "offset_start": 20,
                "offset_end": 37,
                "canonical": {
                    "canonical_address": "501 EAST 5TH STREET CA",
                    "street_number": "501",
                    "street_name": "5TH",
                    "suffix": "STREET",
                    "city": None,
                    "zip": None,
                },
                "confidence": 0.8,
                "metadata": {"parser": "usaddress", "source": "title"},
            }
        ],
    }
    postgres_session.add(article)
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(
            candidate_address="501 E. 5th Street",
            passage_excerpts=[
                {
                    "field": "candidate_address",
                    "value": "501 E. 5th Street",
                    "passage": "The project rises at 501 E. 5th Street.",
                    "offset_start": 20,
                    "offset_end": 37,
                }
            ],
        ),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add(extraction)
    postgres_session.flush()

    decision = decide_pass3a_reextraction(article, extraction)

    assert decision is None


def test_decide_pass3a_reextraction_uses_unit_tolerance(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "142-unit",
                "offset_start": 36,
                "offset_end": 44,
                "canonical": 142,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
    postgres_session.add(article)
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(candidate_unit_total=140),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add(extraction)
    postgres_session.flush()

    decision = decide_pass3a_reextraction(article, extraction)

    assert decision is None


def test_decide_pass3a_reextraction_uses_field_specific_windows(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "95-unit",
                "offset_start": 10,
                "offset_end": 17,
                "canonical": 95,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
    postgres_session.add(article)
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(
            candidate_unit_total=140,
            passage_excerpts=[
                {
                    "field": "candidate_name",
                    "value": "Helio",
                    "passage": "A nearby 95-unit project is unrelated to Helio.",
                    "offset_start": 0,
                    "offset_end": 40,
                },
                {
                    "field": "candidate_unit_total",
                    "value": 140,
                    "passage": "Helio will include 140 units.",
                    "offset_start": 120,
                    "offset_end": 150,
                },
            ],
        ),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add(extraction)
    postgres_session.flush()

    decision = decide_pass3a_reextraction(article, extraction)

    assert decision is None


def test_decide_pass3a_reextraction_detects_legacy_project_dict_hint_conflict(
    postgres_session: Session,
) -> None:
    # The active extract_v2 prompt no longer asks the LLM for registry hints.
    # This remains coverage for legacy rows and external/paste-a-link payloads
    # that still provide a registry_project_id hint.
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    structural_project_id = uuid.uuid4()
    llm_project_id = uuid.uuid4()
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "project_dict",
                "raw_match": "Helio",
                "offset_start": 35,
                "offset_end": 40,
                "canonical": str(structural_project_id),
                "confidence": 0.82,
                "metadata": {"display_name": "Helio"},
            }
        ],
    }
    postgres_session.add(article)
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(registry_project_id=str(llm_project_id)),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add(extraction)
    postgres_session.flush()

    decision = decide_pass3a_reextraction(article, extraction)

    assert decision is not None
    assert decision.triggered_by == "pass1_pass2_conflict"
    conflict = decision.context["conflicts"][0]
    assert conflict["field"] == "registry_project_id"
    assert conflict["structural_value"] == str(structural_project_id)
    assert conflict["extracted_value"] == str(llm_project_id)


def test_extraction_reservation_estimate_covers_opus_cache_miss_headroom() -> None:
    assert EXTRACTION_ESTIMATED_COST_USD == Decimal("0.75")


def test_render_news_glossary_excludes_inactive_and_deleted_projects(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    unique_id = uuid.uuid4().hex
    active_project = Project(
        canonical_address=f"100 {unique_id[:8]} Main St",
        market=source.market.slug if source.market else "unscoped",
        market_id=source.market_id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=f"Active News Project {unique_id}",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    inactive_project = Project(
        canonical_address=f"200 {unique_id[:8]} Main St",
        market=source.market.slug if source.market else "unscoped",
        market_id=source.market_id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=f"Inactive News Project {unique_id}",
        pipeline_status=PipelineStatus.INACTIVE,
    )
    deleted_project = Project(
        canonical_address=f"300 {unique_id[:8]} Main St",
        market=source.market.slug if source.market else "unscoped",
        market_id=source.market_id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=f"Deleted News Project {unique_id}",
        pipeline_status=PipelineStatus.DELETE_DUPLICATE,
    )
    article = _article(source)
    postgres_session.add_all([active_project, inactive_project, deleted_project, article])
    postgres_session.flush()

    glossary = render_news_glossary(postgres_session, article)

    assert active_project.project_name in glossary
    assert inactive_project.project_name not in glossary
    assert deleted_project.project_name not in glossary


def test_render_extraction_prompt_omits_registry_glossary(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    unique_id = uuid.uuid4().hex
    active_project = Project(
        canonical_address=f"100 {unique_id[:8]} Main St",
        market=source.market.slug if source.market else "unscoped",
        market_id=source.market_id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=f"Glossary Omitted Project {unique_id}",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    article = _article(source)
    postgres_session.add_all([active_project, article])
    postgres_session.flush()

    rendered_prompt = render_extraction_prompt(postgres_session, article)

    assert rendered_prompt.prompt_id == "extract_v2"
    assert len(rendered_prompt.system_blocks) == 2
    assert "Glossary:" not in rendered_prompt.system_text
    assert active_project.project_name not in rendered_prompt.system_text
    assert "Signal flag registry:" in rendered_prompt.system_text
    assert "Do not infer registry_developer_id or registry_project_id" in (
        rendered_prompt.system_text
    )
    assert "Do not use outside knowledge, web knowledge, memory, or assumptions" in (
        rendered_prompt.system_text
    )
    assert "Use Conceptual for first mentions, conference comments, ideas" in (
        rendered_prompt.system_text
    )
    assert "Do not compute market-rate units by subtracting affordable units" in (
        rendered_prompt.system_text
    )
    assert "Do not normalize seasons, quarters, month-only, year-only" in (
        rendered_prompt.system_text
    )
    assert "Permits alone are not Under Construction" in rendered_prompt.system_text
    required = rendered_prompt.schema["properties"]["project_references"]["items"]["required"]
    assert "registry_developer_id" not in required
    assert "registry_project_id" not in required
    diagnostic_properties = rendered_prompt.schema["properties"]["diagnostic"]["properties"]
    assert "items" in diagnostic_properties["structural_disagreements"]
    assert "items" in diagnostic_properties["uncertain_offsets"]


def test_extract_v1_remains_legacy_glossary_prompt() -> None:
    prompt = load_prompt("extract_v1")
    required = prompt.schema["properties"]["project_references"]["items"]["required"]

    assert "A glossary of known developers and projects with IDs" in prompt.system_template
    assert "registry_developer_id" in required
    assert "registry_project_id" in required


def test_render_reextraction_prompt_keeps_legacy_registry_glossary(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    unique_id = uuid.uuid4().hex
    active_project = Project(
        canonical_address=f"200 {unique_id[:8]} Main St",
        market=source.market.slug if source.market else "unscoped",
        market_id=source.market_id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=f"Legacy Reextract Project {unique_id}",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    article = _article(source)
    prior_extraction = NewsExtraction(
        article_id=uuid.uuid4(),
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json=_payload(),
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    postgres_session.add_all([active_project, article])
    postgres_session.flush()

    rendered_prompt = render_reextraction_prompt(
        postgres_session,
        article,
        prior_extraction=prior_extraction,
        trigger_context={"triggered_by": "pass2_low_confidence"},
    )

    assert len(rendered_prompt.system_blocks) == 3
    assert "Glossary:" in rendered_prompt.system_text
    assert active_project.project_name in rendered_prompt.system_text
    assert "Signal flag registry:" in rendered_prompt.system_text


def test_render_extract_retry_prompt_uses_retry_context_without_glossary(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    prior_extraction = NewsExtraction(
        article_id=uuid.uuid4(),
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash="hash",
        model="claude-opus-4-7",
        output_json={"malformed": True},
        parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
        parse_error_text="missing required field",
    )
    postgres_session.add(article)
    postgres_session.flush()

    rendered_prompt = render_extract_retry_prompt(
        postgres_session,
        article,
        prior_extraction=prior_extraction,
        retry_context={"attempt": 1, "max_attempts": 2},
    )

    assert rendered_prompt.prompt_id == "extract_retry_v1"
    assert len(rendered_prompt.system_blocks) == 2
    assert "Glossary:" not in rendered_prompt.system_text
    assert "Signal flag registry:" in rendered_prompt.system_text
    assert "Previous extraction parse status" in rendered_prompt.user_text
    assert "schema_invalid" in rendered_prompt.user_text
    assert "missing required field" in rendered_prompt.user_text
    assert "Retry context" in rendered_prompt.user_text


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
        payload=_payload(candidate_unit_workforce=16),
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
    stale_alert = SystemAlert(
        alert_key="news_ai_gateway_api_key_missing",
        severity="warning",
        scope={"component": "news_extraction"},
        message="AI_GATEWAY_API_KEY is not configured; news extraction is skipped.",
        detail={"skipped_reason": "no_api_key", "provider": "vercel_ai_gateway"},
        raised_at=datetime(2026, 4, 29, 11, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 29, 11, 0, tzinfo=UTC),
    )
    postgres_session.add(stale_alert)
    postgres_session.flush()

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
    assert extraction.prompt_id == "extract_v2"
    assert extraction.model_provider == "anthropic"
    assert extraction.parse_status == NewsExtractionParseStatus.OK.value
    reference = postgres_session.execute(
        select(NewsProjectReference).where(NewsProjectReference.extraction_id == extraction.id)
    ).scalar_one()
    assert reference.candidate_name == "Helio"
    assert reference.candidate_unit_total == 140
    assert reference.candidate_unit_workforce == 16
    assert reference.candidate_signal_flags == {"groundbreaking_announced": True}
    assert reference.candidate_delivery_year_normalized == date(2027, 11, 1)
    assert reference.match_status == "pending"
    cleared_alert = postgres_session.get(SystemAlert, stale_alert.id)
    assert cleared_alert is not None
    assert cleared_alert.cleared_at == datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
    assert cleared_alert.cleared_reason == "news_extraction_llm_call_succeeded"
    cost = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == NewsExtractionPass.EXTRACTION.value,
            LLMCostUsage.provider == "anthropic",
            LLMCostUsage.model == "claude-opus-4-7",
        )
    ).scalar_one()
    assert cost.call_count == 1
    assert cost.input_tokens_uncached == 1000
    assert cost.input_tokens_cache_creation == 100
    assert cost.input_tokens_cached == 200
    assert cost.output_tokens == 50
    assert Decimal(cost.spent_usd) == Decimal("0.006975")


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
            assert "Glossary:" not in prompt.system_text
            assert len(prompt.system_blocks) == 2
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
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == "reserved",
            LLMCostUsage.provider == "_reservation_",
            LLMCostUsage.model == "_reservation_",
        )
    ).scalar_one()
    assert Decimal(reservation.spent_usd) == Decimal("0.000000")


def test_run_news_extraction_for_article_retries_output_quality_failure(
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                assert prompt.prompt_id == "extract_v2"
                return _invalid_json_response()
            assert prompt.prompt_id == "extract_retry_v1"
            assert "Previous extraction parse status" in prompt.user_text
            assert "parse_error" in prompt.user_text
            assert "Glossary:" not in prompt.system_text
            return _llm_response(_payload(candidate_unit_total=145))

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 2
    assert result.extraction_id is not None
    assert result.extract_retry_id is not None
    assert result.extract_retry_attempt_count == 1
    assert result.extract_retry_parse_status == NewsExtractionParseStatus.OK.value
    assert result.extract_retry_reference_count == 1
    assert result.reextraction_id is None
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    initial = postgres_session.get(NewsExtraction, result.extraction_id)
    retry = postgres_session.get(NewsExtraction, result.extract_retry_id)
    assert refreshed_article is not None
    assert initial is not None
    assert retry is not None
    assert refreshed_article.current_extraction_id == retry.id
    assert refreshed_article.current_extraction_version == 1
    assert initial.pass_name == NewsExtractionPass.EXTRACTION.value
    assert initial.parse_status == NewsExtractionParseStatus.PARSE_ERROR.value
    assert retry.pass_name == NewsExtractionPass.EXTRACT_RETRY.value
    assert retry.supersedes_extraction_id == initial.id
    assert retry.triggered_by == "output_quality_retry"
    assert retry.diagnostic["extract_retry_context"]["attempt"] == 1
    reference = postgres_session.execute(
        select(NewsProjectReference).where(NewsProjectReference.extraction_id == retry.id)
    ).scalar_one()
    assert reference.candidate_unit_total == 145
    retry_cost = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == NewsExtractionPass.EXTRACT_RETRY.value,
            LLMCostUsage.provider == "anthropic",
            LLMCostUsage.model == "claude-opus-4-7",
        )
    ).scalar_one()
    assert retry_cost.call_count == 1


def test_run_news_extraction_for_article_bounds_extract_retry_attempts(
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

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def extract(self, prompt):  # type: ignore[no-untyped-def]
            self.prompts.append(prompt.prompt_id)
            return _invalid_json_response()

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.prompts == ["extract_v2", "extract_retry_v1", "extract_retry_v1"]
    assert result.extract_retry_id is not None
    assert result.extract_retry_attempt_count == 2
    assert result.extract_retry_parse_status == NewsExtractionParseStatus.PARSE_ERROR.value
    assert result.extract_retry_reference_count == 0
    assert result.reextraction_id is None
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id is None
    assert refreshed_article.current_extraction_version == 0
    retry_rows = (
        postgres_session.execute(
            select(NewsExtraction).where(
                NewsExtraction.article_id == article.id,
                NewsExtraction.pass_name == NewsExtractionPass.EXTRACT_RETRY.value,
            )
        )
        .scalars()
        .all()
    )
    assert len(retry_rows) == 2
    first_retry = next(
        row for row in retry_rows if row.supersedes_extraction_id == result.extraction_id
    )
    second_retry = next(row for row in retry_rows if row.supersedes_extraction_id == first_retry.id)
    assert second_retry.id == result.extract_retry_id
    assert all(
        row.parse_status == NewsExtractionParseStatus.PARSE_ERROR.value for row in retry_rows
    )
    retry_cost = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == NewsExtractionPass.EXTRACT_RETRY.value,
            LLMCostUsage.provider == "anthropic",
            LLMCostUsage.model == "claude-opus-4-7",
        )
    ).scalar_one()
    assert retry_cost.call_count == 2


def test_run_news_extraction_for_article_runs_pass3a_reextraction_on_conflict(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "310-unit",
                "offset_start": 36,
                "offset_end": 44,
                "canonical": 310,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                assert prompt.prompt_id == "extract_v2"
                assert "Glossary:" not in prompt.system_text
                assert len(prompt.system_blocks) == 2
                payload = _payload(candidate_unit_total=250)
            else:
                assert prompt.prompt_id == "reextract_v1"
                assert "Glossary:" in prompt.system_text
                assert len(prompt.system_blocks) == 3
                assert "Re-extraction trigger context" in prompt.user_text
                assert "pass1_pass2_conflict" in prompt.user_text
                payload = _payload(candidate_unit_total=310)
            return ExtractionLLMResponse(
                payload=payload,
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

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 2
    assert result.extraction_id is not None
    assert result.reextraction_id is not None
    assert result.reextraction_triggered_by == "pass1_pass2_conflict"
    assert result.reextraction_parse_status == NewsExtractionParseStatus.OK.value
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.reextraction_id
    assert refreshed_article.current_extraction_version == 2
    initial = postgres_session.get(NewsExtraction, result.extraction_id)
    reextraction = postgres_session.get(NewsExtraction, result.reextraction_id)
    assert initial is not None
    assert reextraction is not None
    assert initial.pass_name == NewsExtractionPass.EXTRACTION.value
    assert reextraction.pass_name == NewsExtractionPass.REEXTRACTION.value
    assert reextraction.supersedes_extraction_id == initial.id
    assert reextraction.triggered_by == "pass1_pass2_conflict"
    assert reextraction.diagnostic["pass3a_context"]["conflicts"][0]["field"] == ("total_units")
    reference = postgres_session.execute(
        select(NewsProjectReference).where(NewsProjectReference.extraction_id == reextraction.id)
    ).scalar_one()
    assert reference.candidate_unit_total == 310
    reextraction_cost = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == date(2026, 4, 29),
            LLMCostUsage.capability == NewsExtractionPass.REEXTRACTION.value,
            LLMCostUsage.provider == "anthropic",
            LLMCostUsage.model == "claude-opus-4-7",
        )
    ).scalar_one()
    assert reextraction_cost.call_count == 1


def test_run_news_extraction_for_article_skips_structural_pass3a_when_agent_route_enabled(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "310-unit",
                "offset_start": 36,
                "offset_end": 44,
                "canonical": 310,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, _prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            return ExtractionLLMResponse(
                payload=_payload(candidate_unit_total=250),
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

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        settings=Settings(
            agent_enabled_for_news=True,
            agent_allow_live_llm=True,
            news_use_legacy_pass3=False,
        ),
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 1
    assert result.extraction_id is not None
    assert result.reextraction_id is None
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    assert refreshed_article.current_extraction_version == 1


def test_run_news_extraction_for_article_does_not_reextract_without_trigger(
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, _prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            return _llm_response(_payload())

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 1
    assert result.extraction_id is not None
    assert result.reextraction_id is None
    assert (
        postgres_session.execute(
            select(LLMCostUsage).where(
                LLMCostUsage.bucket == "news",
                LLMCostUsage.cost_date == date(2026, 4, 29),
                LLMCostUsage.capability == NewsExtractionPass.REEXTRACTION.value,
                LLMCostUsage.provider == "anthropic",
                LLMCostUsage.model == "claude-opus-4-7",
            )
        ).scalar_one_or_none()
        is None
    )


def test_run_news_extraction_for_article_skips_low_confidence_pass3a_by_default(
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, _prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            return _llm_response(_payload(candidate_confidence="low"))

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        settings=Settings(news_use_legacy_pass3=False),
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 1
    assert result.extraction_id is not None
    assert result.reextraction_id is None
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    assert refreshed_article.current_extraction_version == 1
    assert (
        postgres_session.execute(
            select(NewsExtraction).where(
                NewsExtraction.article_id == article.id,
                NewsExtraction.pass_name == NewsExtractionPass.REEXTRACTION.value,
            )
        ).scalar_one_or_none()
        is None
    )
    assert (
        postgres_session.execute(
            select(LLMCostUsage).where(
                LLMCostUsage.bucket == "news",
                LLMCostUsage.cost_date == date(2026, 4, 29),
                LLMCostUsage.capability == NewsExtractionPass.REEXTRACTION.value,
                LLMCostUsage.provider == "anthropic",
                LLMCostUsage.model == "claude-opus-4-7",
            )
        ).scalar_one_or_none()
        is None
    )


def test_run_news_extraction_for_article_runs_low_confidence_pass3a_when_legacy_enabled(
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                assert prompt.prompt_id == "extract_v2"
                return _llm_response(_payload(candidate_confidence="low"))
            assert prompt.prompt_id == "reextract_v1"
            assert "pass2_low_confidence" in prompt.user_text
            return _llm_response(_payload(candidate_confidence="medium"))

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        settings=Settings(news_use_legacy_pass3=True),
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 2
    assert result.extraction_id is not None
    assert result.reextraction_id is not None
    assert result.reextraction_triggered_by == "pass2_low_confidence"
    assert result.reextraction_parse_status == NewsExtractionParseStatus.OK.value
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.reextraction_id
    assert refreshed_article.current_extraction_version == 2
    reextraction = postgres_session.get(NewsExtraction, result.reextraction_id)
    assert reextraction is not None
    assert reextraction.pass_name == NewsExtractionPass.REEXTRACTION.value
    assert reextraction.triggered_by == "pass2_low_confidence"
    assert reextraction.diagnostic["pass3a_context"]["low_confidence"][0]["fields"]


def test_run_news_extraction_for_article_skips_pass3a_when_cost_cap_blocks(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    _set_cost_cap(
        postgres_session,
        effective_date=date(2026, 4, 29),
        warn_usd=Decimal("0.01"),
        hard_usd=Decimal("0.76"),
    )
    source = _news_source(postgres_session)
    article = _article(source)
    _set_unit_conflict_signal(article)
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, _prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            return _llm_response(
                _payload(candidate_unit_total=250),
                usage=LLMUsage(
                    input_tokens_uncached=2000,
                    input_tokens_cache_creation=0,
                    input_tokens_cached=0,
                    output_tokens=1000,
                ),
            )

    client = FakeExtractionClient()

    result = run_news_extraction_for_article(
        article.id,
        client=client,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert client.calls == 1
    assert result.reextraction_id is None
    assert result.reextraction_skipped_reason == "cost_cap"
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_article is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    assert refreshed_article.current_extraction_version == 1
    assert (
        postgres_session.execute(
            select(NewsExtraction).where(
                NewsExtraction.article_id == article.id,
                NewsExtraction.pass_name == NewsExtractionPass.REEXTRACTION.value,
            )
        ).scalar_one_or_none()
        is None
    )


def test_run_news_extraction_for_article_persists_pass3a_api_error_without_advancing(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    _set_unit_conflict_signal(article)
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, _prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return _llm_response(_payload(candidate_unit_total=250))
            raise RuntimeError("pass3 outage")

    result = run_news_extraction_for_article(
        article.id,
        client=FakeExtractionClient(),
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert result.extraction_id is not None
    assert result.reextraction_id is not None
    assert result.reextraction_skipped_reason == "error"
    assert result.reextraction_error_text == "pass3 outage"
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    reextraction = postgres_session.get(NewsExtraction, result.reextraction_id)
    assert refreshed_article is not None
    assert reextraction is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    assert refreshed_article.current_extraction_version == 1
    assert reextraction.pass_name == NewsExtractionPass.REEXTRACTION.value
    assert reextraction.parse_status == NewsExtractionParseStatus.PARSE_ERROR.value
    assert reextraction.supersedes_extraction_id == result.extraction_id
    assert reextraction.diagnostic["stage"] == "api_error"


def test_run_news_extraction_for_article_records_failed_pass3a_parse_without_advancing(
    postgres_session: Session,
) -> None:
    _ensure_news_extraction_tables(postgres_session)
    source = _news_source(postgres_session)
    article = _article(source)
    _set_unit_conflict_signal(article)
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

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, _prompt):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return _llm_response(_payload(candidate_unit_total=250))
            return _llm_response(
                _payload(candidate_unit_total=310),
                stop_reason="max_tokens",
            )

    result = run_news_extraction_for_article(
        article.id,
        client=FakeExtractionClient(),
        session_factory=task_session_factory,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    assert result.extraction_id is not None
    assert result.reextraction_id is not None
    assert result.reextraction_parse_status == NewsExtractionParseStatus.TRUNCATED.value
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    reextraction = postgres_session.get(NewsExtraction, result.reextraction_id)
    assert refreshed_article is not None
    assert reextraction is not None
    assert refreshed_article.current_extraction_id == result.extraction_id
    assert refreshed_article.current_extraction_version == 1
    assert reextraction.pass_name == NewsExtractionPass.REEXTRACTION.value
    assert reextraction.parse_status == NewsExtractionParseStatus.TRUNCATED.value
    assert reextraction.supersedes_extraction_id == result.extraction_id


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


def _llm_response(
    payload: dict,
    *,
    usage: LLMUsage | None = None,
    stop_reason: str = "tool_use",
) -> ExtractionLLMResponse:
    return ExtractionLLMResponse(
        payload=payload,
        text="{}",
        model="claude-opus-4-7",
        usage=usage
        or LLMUsage(
            input_tokens_uncached=100,
            input_tokens_cache_creation=0,
            input_tokens_cached=10,
            output_tokens=20,
        ),
        latency_ms=100,
        stop_reason=stop_reason,
    )


def _invalid_json_response() -> ExtractionLLMResponse:
    return ExtractionLLMResponse(
        payload=None,
        text="{not valid json",
        model="claude-opus-4-7",
        usage=LLMUsage(
            input_tokens_uncached=100,
            input_tokens_cache_creation=0,
            input_tokens_cached=10,
            output_tokens=20,
        ),
        latency_ms=100,
        stop_reason="tool_use",
    )


def _set_unit_conflict_signal(article: NewsArticle) -> None:
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "310-unit",
                "offset_start": 36,
                "offset_end": 44,
                "canonical": 310,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }


def _set_cost_cap(
    postgres_session: Session,
    *,
    effective_date: date,
    warn_usd: Decimal,
    hard_usd: Decimal,
) -> None:
    cap = postgres_session.execute(
        select(CostCap).where(
            CostCap.bucket == "news",
            CostCap.effective_from == effective_date,
        )
    ).scalar_one_or_none()
    if cap is None:
        cap = CostCap(
            bucket="news",
            effective_from=effective_date,
            daily_warn_usd=warn_usd,
            daily_hard_usd=hard_usd,
        )
        postgres_session.add(cap)
    else:
        cap.daily_warn_usd = warn_usd
        cap.daily_hard_usd = hard_usd
    postgres_session.flush()


def _payload(
    *,
    candidate_signal_flags: dict[str, bool] | None = None,
    candidate_address: str = "1234 Sunset Boulevard",
    candidate_unit_total: int = 140,
    candidate_unit_workforce: int | None = None,
    candidate_confidence: str = "high",
    passage_excerpts: list[dict] | None = None,
    registry_project_id: str | None = None,
) -> dict:
    resolved_passage_excerpts = passage_excerpts or [
        {
            "field": "candidate_name",
            "value": "Helio",
            "passage": "Atlas Development broke ground on Helio",
            "offset_start": 0,
            "offset_end": 40,
        },
        {
            "field": "candidate_unit_total",
            "value": 140,
            "passage": "The developer broke ground on a 140-unit project.",
            "offset_start": 34,
            "offset_end": 42,
        },
    ]
    return {
        "relevance": "confirmed",
        "rejected_reason": None,
        "project_references": [
            {
                "candidate_name": "Helio",
                "candidate_address": candidate_address,
                "candidate_developer": "Atlas Development",
                "candidate_unit_total": candidate_unit_total,
                "candidate_unit_affordable": 14,
                "candidate_unit_market_rate": 126,
                "candidate_unit_workforce": candidate_unit_workforce,
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
                "candidate_confidence": candidate_confidence,
                "passage_excerpts": resolved_passage_excerpts,
                "registry_developer_id": None,
                "registry_project_id": registry_project_id,
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
        "llm_cost_usage",
        "cost_caps",
        "news_signal_flag_registry",
        "system_alerts",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running extraction tests: {missing}")


def _news_source(postgres_session: Session) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "news_paste_a_link")
    ).scalar_one_or_none()
    if source is None:
        pytest.skip("Apply migration 202604290021 before running extraction tests.")
    if not postgres_session.execute(
        select(NewsSignalFlag).where(NewsSignalFlag.flag_key == "groundbreaking_announced")
    ).scalar_one_or_none():
        pytest.skip("Apply migration 202604290019 before running extraction tests.")
    return source
