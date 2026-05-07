from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    Jurisdiction,
    LLMCostUsage,
    Market,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsProjectReference,
    NewsSemanticInterpretation,
    NewsSource,
)
from tcg_pipeline.news.costs import RESERVATION_MODEL, RESERVATION_PASS_NAME, RESERVATION_PROVIDER
from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC, LLMUsage
from tcg_pipeline.semantic.constants import NEWS_SEMANTIC_CAPABILITY
from tcg_pipeline.semantic.news.pass2c import (
    RenderedInterpretPrompt,
    SemanticLLMResponse,
    load_current_semantic_interpretation_row,
    load_current_semantic_interpretations,
    parse_semantic_response,
    render_interpret_prompt,
    run_news_semantic_interpretation_for_extraction,
)
from tcg_pipeline.semantic.reason_codes import build_reason_code_registry
from tcg_pipeline.settings import Settings


class FakePass2cClient:
    provider = LLM_PROVIDER_ANTHROPIC
    model = DEFAULT_EXTRACTION_MODEL

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[RenderedInterpretPrompt] = []

    def interpret(self, prompt: RenderedInterpretPrompt) -> SemanticLLMResponse:
        self.prompts.append(prompt)
        return SemanticLLMResponse(
            payload=self.payload,
            text="{}",
            model=self.model,
            provider=self.provider,
            usage=LLMUsage(
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=20,
                output_tokens=30,
            ),
            latency_ms=123,
            stop_reason=None,
        )


class RaisingPass2cClient:
    provider = LLM_PROVIDER_ANTHROPIC
    model = DEFAULT_EXTRACTION_MODEL

    def interpret(self, prompt: RenderedInterpretPrompt) -> SemanticLLMResponse:
        raise RuntimeError("provider down")


def test_parse_semantic_response_accepts_registered_reason_code() -> None:
    registry = build_reason_code_registry()

    parsed = parse_semantic_response(
        _semantic_payload(),
        raw_text="{}",
        registry=registry,
    )

    assert parsed.parse_status == NewsExtractionParseStatus.OK.value
    assert parsed.parse_error_text is None
    assert len(parsed.interpretations) == 1
    interpretation = parsed.interpretations[0]
    assert interpretation.field_name == "pipeline_status"
    assert interpretation.canonical_value == "Under Construction"
    assert interpretation.reason_code == "news_topped_out"
    assert interpretation.source_anchors[0].text == "the tower has topped out"


def test_parse_semantic_response_rejects_unknown_reason_code() -> None:
    registry = build_reason_code_registry()
    payload = _semantic_payload(reason_code="news_not_real")

    parsed = parse_semantic_response(payload, raw_text="{}", registry=registry)

    assert parsed.parse_status == NewsExtractionParseStatus.SCHEMA_INVALID.value
    assert "Unknown semantic reason_code" in (parsed.parse_error_text or "")


@pytest.mark.parametrize("stop_reason", ["max_tokens", "length", "max_output_tokens"])
def test_parse_semantic_response_marks_truncation_stop_reasons(stop_reason: str) -> None:
    parsed = parse_semantic_response(
        _semantic_payload(),
        raw_text="{}",
        stop_reason=stop_reason,
        registry=build_reason_code_registry(),
    )

    assert parsed.parse_status == NewsExtractionParseStatus.TRUNCATED.value
    assert parsed.parse_error_text == stop_reason


def test_parse_semantic_response_marks_refusal_stop_reason() -> None:
    parsed = parse_semantic_response(
        _semantic_payload(),
        raw_text="{}",
        stop_reason="refusal",
        registry=build_reason_code_registry(),
    )

    assert parsed.parse_status == NewsExtractionParseStatus.REFUSED.value
    assert parsed.parse_error_text == "refusal"


def test_parse_semantic_response_records_root_array_recovery() -> None:
    registry = build_reason_code_registry()
    raw_text = json.dumps(_semantic_payload()["interpretations"])

    parsed = parse_semantic_response(None, raw_text=raw_text, registry=registry)

    assert parsed.parse_status == NewsExtractionParseStatus.OK.value
    assert parsed.diagnostic["parser_recovered_root_array"] is True
    assert parsed.payload["diagnostic"]["parser_recovered_root_array"] is True


def test_render_interpret_prompt_uses_default_files(postgres_session: Session) -> None:
    _ensure_semantic_tables(postgres_session)
    article, extraction, reference = _semantic_fixture(postgres_session)

    prompt = render_interpret_prompt(
        postgres_session,
        article=article,
        extraction=extraction,
        references=(reference,),
    )

    assert prompt.prompt_id == "interpret_v1"
    assert prompt.prompt_version == "v1"
    assert prompt.capability_key == NEWS_SEMANTIC_CAPABILITY
    assert "TCG news semantic interpreter" in prompt.system_text
    assert "Reason-code registry:" in prompt.system_text
    assert "TCG news semantic interpreter" in prompt.system_blocks[0]
    assert prompt.system_blocks[1].startswith("Reason-code registry:")
    assert prompt.schema["properties"]["interpretations"]["type"] == "array"
    assert str(reference.id) in prompt.user_text
    assert "Semantic Tower" in prompt.user_text


def test_render_interpret_prompt_includes_loaded_jurisdiction_policy(
    postgres_session: Session,
) -> None:
    _ensure_semantic_tables(postgres_session)
    article, extraction, reference = _semantic_fixture(postgres_session)
    _attach_jurisdiction(postgres_session, article.source, "city_of_los_angeles")

    prompt = render_interpret_prompt(
        postgres_session,
        article=article,
        extraction=extraction,
        references=(reference,),
    )
    user_payload = json.loads(prompt.user_text)

    assert user_payload["fallback_jurisdiction_policy"]["jurisdiction_slug"] == (
        "city_of_los_angeles"
    )
    assert user_payload["fallback_jurisdiction_policy"]["policy_scope"] == (
        "article_source_fallback"
    )
    assert user_payload["fallback_jurisdiction_policy"]["permit_data_quality"] == "high"
    assert user_payload["fallback_jurisdiction_policy"]["news_status_promotion_policy"] == (
        "wait_for_permit_corroboration"
    )


def test_run_pass2c_writes_audit_row_cost_and_current_loader(
    postgres_session: Session,
) -> None:
    _ensure_semantic_tables(postgres_session)
    article, extraction, _reference = _semantic_fixture(postgres_session)
    now = datetime(2099, 1, 1, tzinfo=UTC)
    session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    fake_client = FakePass2cClient(_semantic_payload())

    result = run_news_semantic_interpretation_for_extraction(
        extraction.id,
        settings=Settings(_env_file=None),
        client=fake_client,
        session_factory=session_factory,
        now=now,
    )

    assert result.article_id == article.id
    assert result.extraction_id == extraction.id
    assert result.parse_status == NewsExtractionParseStatus.OK.value
    assert result.interpretation_count == 1
    assert len(fake_client.prompts) == 1

    row = postgres_session.get(NewsSemanticInterpretation, result.semantic_interpretation_id)
    assert row is not None
    assert row.prompt_id == "interpret_v1"
    assert row.parse_status == NewsExtractionParseStatus.OK.value
    assert row.output_json["interpretations"][0]["reason_code"] == "news_topped_out"

    usage = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.capability == NEWS_SEMANTIC_CAPABILITY,
            LLMCostUsage.model == DEFAULT_EXTRACTION_MODEL,
            LLMCostUsage.cost_date == date(2098, 12, 31),
        )
    ).scalar_one()
    assert usage.call_count == 1
    assert usage.input_tokens_uncached == 100
    assert usage.input_tokens_cached == 20
    assert usage.output_tokens == 30

    interpretations = load_current_semantic_interpretations(
        postgres_session,
        extraction.id,
        registry=fake_client.prompts[0].reason_code_registry,
    )
    assert len(interpretations) == 1
    assert interpretations[0].canonical_value == "Under Construction"


def test_run_pass2c_persists_api_error_and_releases_reservation(
    postgres_session: Session,
) -> None:
    _ensure_semantic_tables(postgres_session)
    article, extraction, _reference = _semantic_fixture(postgres_session)
    session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    result = run_news_semantic_interpretation_for_extraction(
        extraction.id,
        settings=Settings(_env_file=None),
        client=RaisingPass2cClient(),
        session_factory=session_factory,
        now=datetime(2099, 1, 2, 12, tzinfo=UTC),
    )

    assert result.article_id == article.id
    assert result.extraction_id == extraction.id
    assert result.parse_status == NewsExtractionParseStatus.PARSE_ERROR.value
    assert result.interpretation_count == 0
    assert result.error_text == "provider down"

    row = postgres_session.get(NewsSemanticInterpretation, result.semantic_interpretation_id)
    assert row is not None
    assert row.parse_status == NewsExtractionParseStatus.PARSE_ERROR.value
    assert row.parse_error_text == "provider down"
    assert row.diagnostic["error_type"] == "RuntimeError"

    reservation = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.cost_date == date(2099, 1, 2),
            LLMCostUsage.capability == RESERVATION_PASS_NAME,
            LLMCostUsage.provider == RESERVATION_PROVIDER,
            LLMCostUsage.model == RESERVATION_MODEL,
        )
    ).scalar_one()
    assert Decimal(reservation.spent_usd) == Decimal("0.000000")


def test_current_semantic_interpretation_loader_uses_latest_ok_row(
    postgres_session: Session,
) -> None:
    _ensure_semantic_tables(postgres_session)
    article, extraction, _reference = _semantic_fixture(postgres_session)
    older_ok = _semantic_row(
        article=article,
        extraction=extraction,
        parse_status=NewsExtractionParseStatus.OK.value,
        created_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    newer_failed = _semantic_row(
        article=article,
        extraction=extraction,
        parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
        created_at=datetime(2099, 1, 3, tzinfo=UTC),
    )
    newer_ok = _semantic_row(
        article=article,
        extraction=extraction,
        parse_status=NewsExtractionParseStatus.OK.value,
        created_at=datetime(2099, 1, 2, tzinfo=UTC),
    )
    postgres_session.add_all([older_ok, newer_failed, newer_ok])
    postgres_session.flush()

    row = load_current_semantic_interpretation_row(postgres_session, extraction.id)

    assert row is not None
    assert row.id == newer_ok.id


def _ensure_semantic_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    missing = {
        "news_semantic_interpretations",
        "news_extractions",
        "news_project_references",
        "news_articles",
        "news_sources",
        "llm_cost_usage",
    } - set(inspector.get_table_names())
    assert not missing


def _semantic_fixture(
    session: Session,
) -> tuple[NewsArticle, NewsExtraction, NewsProjectReference]:
    suffix = uuid.uuid4().hex
    source = NewsSource(
        slug=f"semantic-source-{suffix}",
        name="Semantic Source",
        base_url="https://example.com",
        collector_class="manual",
    )
    article = NewsArticle(
        source=source,
        url_canonical=f"https://example.com/{suffix}",
        url_original=f"https://example.com/{suffix}",
        url_hash=suffix,
        ingest_method="news_paste_a_link",
        title="Semantic Tower tops out",
        body_text="Semantic Tower has topped out above Main Street.",
    )
    session.add_all([source, article])
    session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash=uuid.uuid4().hex,
        model=DEFAULT_EXTRACTION_MODEL,
        output_json={
            "relevance": "confirmed",
            "project_references": [],
            "diagnostic": {},
        },
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    session.add(extraction)
    session.flush()
    reference = NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=0,
        candidate_name="Semantic Tower",
        candidate_confidence="high",
        candidate_signal_flags={},
        candidate_identifiers={"case_number": [], "permit_number": [], "apn": []},
        passage_excerpts=[],
    )
    session.add(reference)
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    session.flush()
    return article, extraction, reference


def _attach_jurisdiction(
    session: Session,
    source: NewsSource,
    jurisdiction_slug: str,
) -> None:
    jurisdiction = session.execute(
        select(Jurisdiction).where(Jurisdiction.slug == jurisdiction_slug).limit(1)
    ).scalar_one_or_none()
    if jurisdiction is None:
        suffix = uuid.uuid4().hex
        market = Market(
            slug=f"semantic-market-{suffix}",
            name="Semantic Market",
            state="CA",
        )
        jurisdiction = Jurisdiction(
            slug=jurisdiction_slug,
            name="City of Los Angeles",
            state="CA",
            market=market,
        )
        session.add_all([market, jurisdiction])
        session.flush()
    source.market = jurisdiction.market
    source.jurisdiction = jurisdiction
    session.flush()


def _semantic_payload(reason_code: str = "news_topped_out") -> dict:
    return {
        "interpretations": [
            {
                "field_name": "pipeline_status",
                "canonical_value": "Under Construction",
                "confidence": "high",
                "reason_code": reason_code,
                "signal_flags": {"topped_out": True},
                "source_anchors": [
                    {
                        "text": "the tower has topped out",
                        "offset_start": 10,
                        "offset_end": 34,
                        "field_name": "pipeline_status",
                        "metadata": {},
                    }
                ],
                "requires_corroboration": False,
                "metadata": {"tense": "past_concurrent"},
            }
        ],
        "diagnostic": {"fixture": True},
    }


def _semantic_row(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    parse_status: str,
    created_at: datetime,
) -> NewsSemanticInterpretation:
    return NewsSemanticInterpretation(
        article_id=article.id,
        extraction_id=extraction.id,
        prompt_id="interpret_v1",
        prompt_version="v1",
        prompt_hash=uuid.uuid4().hex,
        model=DEFAULT_EXTRACTION_MODEL,
        model_provider=LLM_PROVIDER_ANTHROPIC,
        output_json=_semantic_payload(),
        parse_status=parse_status,
        created_at=created_at,
    )
