from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsMatchStatus,
    NewsProjectReference,
    NewsSignalFlag,
    NewsTriageStatus,
    SystemAlert,
)
from tcg_pipeline.matching.normalizer import normalize_address
from tcg_pipeline.news.costs import (
    record_llm_cost,
    release_llm_cost_reservation,
    reserve_llm_cost,
)
from tcg_pipeline.news.llm import (
    DEFAULT_EXTRACTION_MODEL,
    LLMUsage,
    anthropic_usage,
    calculate_llm_cost_usd,
    pricing_for_model,
)
from tcg_pipeline.news.prompts import (
    RenderedPrompt,
    render_extraction_prompt,
    render_reextraction_prompt,
)
from tcg_pipeline.settings import Settings, get_settings

EXTRACTION_TRIGGERED_BY = "initial"
PASS3A_TRIGGER_PASS1_PASS2_CONFLICT = "pass1_pass2_conflict"
PASS3A_TRIGGER_LOW_CONFIDENCE = "pass2_low_confidence"
PASS3A_TRIGGER_PARSE_ERROR = "pass2_parse_error"
EXTRACTION_ESTIMATED_COST_USD = Decimal("0.75")
REEXTRACTION_ESTIMATED_COST_USD = Decimal("0.75")
EXTRACTION_TEMPERATURE = 0
EXTRACTION_MAX_TOKENS = 2500
EXTRACTION_TOOL_NAME = "emit_project_extraction"
PASS3A_PARSE_TRIGGER_STATUSES = frozenset(
    {
        NewsExtractionParseStatus.PARSE_ERROR.value,
        NewsExtractionParseStatus.SCHEMA_INVALID.value,
        NewsExtractionParseStatus.REFUSED.value,
        NewsExtractionParseStatus.TRUNCATED.value,
    }
)
PASS3A_REFERENCE_FIELD_MAP = {
    "pipeline_status": "candidate_status_signal",
    "total_units": "candidate_unit_total",
    "affordable_units": "candidate_unit_affordable",
    "market_rate_units": "candidate_unit_market_rate",
    "developer": "candidate_developer",
    "date_delivery": "candidate_delivery_year_normalized",
    "candidate_address": "candidate_address",
}
MAX_PASS3A_CONTEXT_ITEMS = 20


@dataclass(frozen=True, slots=True)
class ExtractionLLMResponse:
    payload: dict[str, Any] | None
    text: str
    model: str
    usage: LLMUsage
    latency_ms: int
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedExtractionResponse:
    payload: dict[str, Any] | None
    parse_status: str
    parse_error_text: str | None
    unknown_signal_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewsExtractionRunResult:
    article_id: uuid.UUID
    extraction_id: uuid.UUID | None
    relevance: str | None
    reference_count: int
    parse_status: str | None
    skipped_reason: str | None = None
    error_text: str | None = None
    triggered_by: str | None = None
    reextraction_id: uuid.UUID | None = None
    reextraction_triggered_by: str | None = None
    reextraction_reference_count: int | None = None
    reextraction_parse_status: str | None = None
    reextraction_skipped_reason: str | None = None
    reextraction_error_text: str | None = None


@dataclass(frozen=True, slots=True)
class Pass3aDecision:
    triggered_by: str
    context: dict[str, Any]


class ExtractionLLMClient(Protocol):
    model: str

    def extract(self, prompt: RenderedPrompt) -> ExtractionLLMResponse:
        ...


class AnthropicExtractionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EXTRACTION_MODEL,
        max_tokens: int = EXTRACTION_MAX_TOKENS,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def extract(self, prompt: RenderedPrompt) -> ExtractionLLMResponse:
        started_at = time.perf_counter()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            temperature=EXTRACTION_TEMPERATURE,
            system=_cacheable_system_blocks(prompt),
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt.user_text}],
                }
            ],
            tools=[
                {
                    "name": EXTRACTION_TOOL_NAME,
                    "description": "Emit structured real estate project references.",
                    "input_schema": prompt.schema,
                }
            ],
            tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        payload: dict[str, Any] | None = None
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use" and getattr(block, "name", None) == EXTRACTION_TOOL_NAME:
                block_input = getattr(block, "input", None)
                if isinstance(block_input, dict):
                    payload = block_input
            elif block_type == "text":
                text_parts.append(getattr(block, "text", ""))
        raw_text = (
            json.dumps(payload, sort_keys=True)
            if payload is not None
            else "\n".join(text_parts)
        )
        return ExtractionLLMResponse(
            payload=payload,
            text=raw_text,
            model=response.model,
            usage=anthropic_usage(response.usage),
            latency_ms=latency_ms,
            stop_reason=response.stop_reason,
        )


def _cacheable_system_blocks(prompt: RenderedPrompt) -> list[dict[str, object]]:
    blocks = prompt.system_blocks or (prompt.system_text,)
    return [
        {
            "type": "text",
            "text": block,
            "cache_control": {"type": "ephemeral"},
        }
        for block in blocks
    ]


class PassageExcerptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: Any
    passage: str
    offset_start: int = Field(ge=0)
    offset_end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> PassageExcerptPayload:
        if self.offset_end < self.offset_start:
            raise ValueError("offset_end must be greater than or equal to offset_start")
        return self


class CandidateIdentifiersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_number: list[str] = Field(default_factory=list)
    permit_number: list[str] = Field(default_factory=list)
    apn: list[str] = Field(default_factory=list)


class ProjectReferencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_name: str | None = None
    candidate_address: str | None = None
    candidate_developer: str | None = None
    candidate_unit_total: int | None = Field(default=None, ge=0)
    candidate_unit_affordable: int | None = Field(default=None, ge=0)
    candidate_unit_market_rate: int | None = Field(default=None, ge=0)
    candidate_product_type: (
        Literal["apartment", "condo", "townhome", "single_family", "micro_co_living", "other"]
        | None
    ) = None
    candidate_age_restriction: (
        Literal["non_age_restricted", "senior", "student", "unknown"] | None
    ) = None
    candidate_status_signal: (
        Literal[
            "Conceptual",
            "Proposed",
            "Pending",
            "Approved",
            "Under Construction",
            "Pre-Leasing/Pre-Selling",
            "Complete",
            "Stalled",
            "Inactive",
        ]
        | None
    ) = None
    candidate_delivery_year_text: str | None = None
    candidate_delivery_year_normalized: date | None = None
    candidate_signal_flags: dict[str, bool] = Field(default_factory=dict)
    candidate_identifiers: CandidateIdentifiersPayload = Field(
        default_factory=CandidateIdentifiersPayload
    )
    candidate_neighborhood: str | None = None
    candidate_lat: float | None = None
    candidate_lng: float | None = None
    candidate_confidence: Literal["high", "medium", "low"]
    passage_excerpts: list[PassageExcerptPayload] = Field(default_factory=list)
    registry_developer_id: uuid.UUID | None = None
    registry_project_id: uuid.UUID | None = None

    @field_validator(
        "candidate_name",
        "candidate_address",
        "candidate_developer",
        "candidate_delivery_year_text",
        "candidate_neighborhood",
        mode="before",
    )
    @classmethod
    def blank_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ExtractionOutputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevance: Literal["confirmed", "rejected", "unclear"]
    rejected_reason: str | None = None
    project_references: list[ProjectReferencePayload] = Field(default_factory=list)
    diagnostic: dict[str, Any] = Field(default_factory=dict)


def run_news_extraction_for_article(
    article_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    client: ExtractionLLMClient | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
) -> NewsExtractionRunResult:
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or get_session_factory()
    current = now or datetime.now(UTC)
    with resolved_session_factory() as session:
        article = session.get(NewsArticle, article_id)
        if article is None:
            raise RuntimeError("News extraction references a missing article.")
        skipped_reason = _article_skip_reason(article)
        if skipped_reason is not None:
            return NewsExtractionRunResult(
                article_id=article_id,
                extraction_id=article.current_extraction_id,
                relevance=None,
                reference_count=0,
                parse_status=None,
                skipped_reason=skipped_reason,
            )
        rendered_prompt = render_extraction_prompt(session, article)

    if client is None and not resolved_settings.anthropic_api_key:
        with resolved_session_factory() as session:
            _raise_missing_api_key_alert(session, now=current)
            session.commit()
        return NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=None,
            relevance=None,
            reference_count=0,
            parse_status=None,
            skipped_reason="no_api_key",
        )

    extraction_client = client or build_anthropic_extraction_client(resolved_settings)
    pricing_for_model(extraction_client.model)
    with resolved_session_factory() as session:
        reservation = reserve_llm_cost(
            session,
            pass_name=NewsExtractionPass.EXTRACTION.value,
            model=extraction_client.model,
            estimated_cost_usd=EXTRACTION_ESTIMATED_COST_USD,
            now=current,
        )
        session.commit()
    if reservation is None:
        return NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=None,
            relevance=None,
            reference_count=0,
            parse_status=None,
            skipped_reason="cost_cap",
        )

    try:
        llm_response = extraction_client.extract(rendered_prompt)
    except Exception as exc:
        with resolved_session_factory() as session:
            release_llm_cost_reservation(
                session,
                reserved_cost_usd=EXTRACTION_ESTIMATED_COST_USD,
                now=current,
            )
            _persist_extraction_api_error(
                session,
                article_id=article_id,
                rendered_prompt=rendered_prompt,
                model=extraction_client.model,
                error=exc,
                now=current,
            )
            session.commit()
        raise RuntimeError("News extraction LLM call failed.") from exc

    with resolved_session_factory() as session:
        result = persist_extraction_response(
            session,
            article_id=article_id,
            rendered_prompt=rendered_prompt,
            llm_response=llm_response,
            reserved_cost_usd=EXTRACTION_ESTIMATED_COST_USD,
            now=current,
        )
        session.commit()
    pass3a_result = _maybe_run_pass3a_reextraction(
        article_id=article_id,
        prior_result=result,
        client=extraction_client,
        session_factory=resolved_session_factory,
        now=current,
    )
    if pass3a_result is None:
        return result
    return _merge_pass3a_result(result, pass3a_result)


def build_anthropic_extraction_client(settings: Settings) -> AnthropicExtractionClient:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for news extraction.")
    return AnthropicExtractionClient(
        api_key=settings.anthropic_api_key,
        model=settings.news_extract_model,
        max_tokens=settings.news_extract_max_tokens,
    )


def _maybe_run_pass3a_reextraction(
    *,
    article_id: uuid.UUID,
    prior_result: NewsExtractionRunResult,
    client: ExtractionLLMClient,
    session_factory: sessionmaker[Session],
    now: datetime,
) -> NewsExtractionRunResult | None:
    if prior_result.extraction_id is None:
        return None
    with session_factory() as session:
        article = session.get(NewsArticle, article_id)
        prior_extraction = session.get(NewsExtraction, prior_result.extraction_id)
        if article is None or prior_extraction is None:
            raise RuntimeError("Pass 3a re-extraction references missing rows.")
        decision = decide_pass3a_reextraction(article, prior_extraction)
        if decision is None:
            return None
        rendered_prompt = render_reextraction_prompt(
            session,
            article,
            prior_extraction=prior_extraction,
            trigger_context=decision.context,
        )

    with session_factory() as session:
        reservation = reserve_llm_cost(
            session,
            pass_name=NewsExtractionPass.REEXTRACTION.value,
            model=client.model,
            estimated_cost_usd=REEXTRACTION_ESTIMATED_COST_USD,
            now=now,
        )
        session.commit()
    if reservation is None:
        return NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=None,
            relevance=None,
            reference_count=0,
            parse_status=None,
            skipped_reason="cost_cap",
            triggered_by=decision.triggered_by,
        )

    try:
        llm_response = client.extract(rendered_prompt)
    except Exception as exc:
        with session_factory() as session:
            release_llm_cost_reservation(
                session,
                reserved_cost_usd=REEXTRACTION_ESTIMATED_COST_USD,
                now=now,
            )
            error_result = _persist_extraction_api_error(
                session,
                article_id=article_id,
                rendered_prompt=rendered_prompt,
                model=client.model,
                error=exc,
                now=now,
                pass_name=NewsExtractionPass.REEXTRACTION.value,
                triggered_by=decision.triggered_by,
                supersedes_extraction_id=prior_result.extraction_id,
                extra_diagnostic={"pass3a_context": decision.context},
            )
            session.commit()
        return NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=error_result.extraction_id,
            relevance=None,
            reference_count=0,
            parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
            skipped_reason="error",
            error_text=str(exc),
            triggered_by=decision.triggered_by,
        )

    with session_factory() as session:
        result = persist_extraction_response(
            session,
            article_id=article_id,
            rendered_prompt=rendered_prompt,
            llm_response=llm_response,
            reserved_cost_usd=REEXTRACTION_ESTIMATED_COST_USD,
            now=now,
            pass_name=NewsExtractionPass.REEXTRACTION.value,
            triggered_by=decision.triggered_by,
            supersedes_extraction_id=prior_result.extraction_id,
            extra_diagnostic={"pass3a_context": decision.context},
        )
        session.commit()
    return result


def _merge_pass3a_result(
    prior_result: NewsExtractionRunResult,
    pass3a_result: NewsExtractionRunResult,
) -> NewsExtractionRunResult:
    return NewsExtractionRunResult(
        article_id=prior_result.article_id,
        extraction_id=prior_result.extraction_id,
        relevance=prior_result.relevance,
        reference_count=prior_result.reference_count,
        parse_status=prior_result.parse_status,
        skipped_reason=prior_result.skipped_reason,
        error_text=prior_result.error_text,
        triggered_by=prior_result.triggered_by,
        reextraction_id=pass3a_result.extraction_id,
        reextraction_triggered_by=pass3a_result.triggered_by,
        reextraction_reference_count=pass3a_result.reference_count,
        reextraction_parse_status=pass3a_result.parse_status,
        reextraction_skipped_reason=pass3a_result.skipped_reason,
        reextraction_error_text=pass3a_result.error_text,
    )


def persist_extraction_response(
    session: Session,
    *,
    article_id: uuid.UUID,
    rendered_prompt: RenderedPrompt,
    llm_response: ExtractionLLMResponse,
    reserved_cost_usd: Decimal = Decimal("0"),
    now: datetime | None = None,
    pass_name: str = NewsExtractionPass.EXTRACTION.value,
    triggered_by: str = EXTRACTION_TRIGGERED_BY,
    supersedes_extraction_id: uuid.UUID | None = None,
    extra_diagnostic: dict[str, Any] | None = None,
) -> NewsExtractionRunResult:
    current = now or datetime.now(UTC)
    active_signal_flags = _active_signal_flag_keys(session)
    parsed = parse_extraction_response(
        llm_response.payload,
        raw_text=llm_response.text,
        stop_reason=llm_response.stop_reason,
        active_signal_flags=active_signal_flags,
    )
    cost_usd = calculate_llm_cost_usd(
        llm_response.model,
        input_tokens_uncached=llm_response.usage.input_tokens_uncached,
        input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
        input_tokens_cached=llm_response.usage.input_tokens_cached,
        output_tokens=llm_response.usage.output_tokens,
    )
    record_llm_cost(
        session,
        pass_name=pass_name,
        model=llm_response.model,
        input_tokens_uncached=llm_response.usage.input_tokens_uncached,
        input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
        input_tokens_cached=llm_response.usage.input_tokens_cached,
        output_tokens=llm_response.usage.output_tokens,
        cost_usd=cost_usd,
        reserved_cost_usd=reserved_cost_usd,
        now=current,
    )
    article = session.execute(
        select(NewsArticle).where(NewsArticle.id == article_id).with_for_update()
    ).scalar_one_or_none()
    if article is None:
        raise RuntimeError("News extraction references a missing article.")
    diagnostic = {"stop_reason": llm_response.stop_reason}
    if extra_diagnostic:
        diagnostic.update(extra_diagnostic)
    if parsed.unknown_signal_flags:
        diagnostic["unknown_signal_flags"] = list(parsed.unknown_signal_flags)
    extraction = NewsExtraction(
        article_id=article_id,
        pass_name=pass_name,
        triggered_by=triggered_by,
        supersedes_extraction_id=supersedes_extraction_id,
        prompt_id=rendered_prompt.prompt_id,
        prompt_version=rendered_prompt.prompt_version,
        prompt_hash=rendered_prompt.prompt_hash,
        model=llm_response.model,
        input_tokens_uncached=llm_response.usage.input_tokens_uncached,
        input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
        input_tokens_cached=llm_response.usage.input_tokens_cached,
        output_tokens=llm_response.usage.output_tokens,
        cost_usd=cost_usd,
        latency_ms=llm_response.latency_ms,
        output_json=parsed.payload,
        raw_response_text=llm_response.text,
        parse_status=parsed.parse_status,
        parse_error_text=parsed.parse_error_text,
        diagnostic=diagnostic,
    )
    session.add(extraction)
    session.flush()

    reference_count = 0
    relevance: str | None = None
    if parsed.payload is not None and parsed.parse_status == NewsExtractionParseStatus.OK.value:
        relevance = str(parsed.payload["relevance"])
        references = parsed.payload.get("project_references") or []
        for index, reference_payload in enumerate(references):
            reference = _reference_from_payload(
                article_id=article_id,
                extraction_id=extraction.id,
                reference_index=index,
                payload=reference_payload,
            )
            session.add(reference)
            reference_count += 1
        article.current_extraction_id = extraction.id
        article.current_extraction_version = (article.current_extraction_version or 0) + 1
        session.flush()
    return NewsExtractionRunResult(
        article_id=article_id,
        extraction_id=extraction.id,
        relevance=relevance,
        reference_count=reference_count,
        parse_status=parsed.parse_status,
        triggered_by=triggered_by,
    )


def parse_extraction_response(
    payload: dict[str, Any] | None,
    *,
    raw_text: str,
    stop_reason: str | None = None,
    active_signal_flags: set[str] | None = None,
) -> ParsedExtractionResponse:
    if stop_reason == "max_tokens":
        return ParsedExtractionResponse(
            payload=None,
            parse_status=NewsExtractionParseStatus.TRUNCATED.value,
            parse_error_text=stop_reason,
        )
    if stop_reason == "refusal":
        return ParsedExtractionResponse(
            payload=None,
            parse_status=NewsExtractionParseStatus.REFUSED.value,
            parse_error_text=stop_reason,
        )
    candidate_payload = payload
    if candidate_payload is None:
        try:
            loaded = json.loads(_strip_json_fence(raw_text).strip())
        except json.JSONDecodeError as exc:
            return ParsedExtractionResponse(
                payload=None,
                parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
                parse_error_text=str(exc),
            )
        if not isinstance(loaded, dict):
            return ParsedExtractionResponse(
                payload={"value": loaded},
                parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
                parse_error_text="Extraction response root must be an object.",
            )
        candidate_payload = loaded
    try:
        parsed = ExtractionOutputPayload.model_validate(candidate_payload)
    except ValidationError as exc:
        return ParsedExtractionResponse(
            payload=candidate_payload,
            parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
            parse_error_text=str(exc),
        )
    normalized_payload, unknown_signal_flags = _normalized_extraction_payload(
        parsed,
        active_signal_flags=active_signal_flags or set(),
    )
    return ParsedExtractionResponse(
        payload=normalized_payload,
        parse_status=NewsExtractionParseStatus.OK.value,
        parse_error_text=None,
        unknown_signal_flags=tuple(sorted(unknown_signal_flags)),
    )


def decide_pass3a_reextraction(
    article: NewsArticle,
    extraction: NewsExtraction,
) -> Pass3aDecision | None:
    context: dict[str, Any] = {
        "previous_extraction_id": str(extraction.id),
        "previous_parse_status": extraction.parse_status,
        "triggers": [],
        "conflicts": [],
        "low_confidence": [],
    }
    if extraction.parse_status in PASS3A_PARSE_TRIGGER_STATUSES:
        context["triggers"].append(PASS3A_TRIGGER_PARSE_ERROR)
        context["parse_error_text"] = extraction.parse_error_text
        return Pass3aDecision(
            triggered_by=PASS3A_TRIGGER_PARSE_ERROR,
            context=_trim_pass3a_context(context),
        )

    payload = extraction.output_json
    if not isinstance(payload, dict):
        return None
    references = payload.get("project_references")
    if not isinstance(references, list):
        return None

    conflicts = _pass3a_structural_conflicts(article, references)
    low_confidence = _pass3a_low_confidence_references(references)
    if conflicts:
        context["triggers"].append(PASS3A_TRIGGER_PASS1_PASS2_CONFLICT)
        context["conflicts"] = conflicts
    if low_confidence:
        context["triggers"].append(PASS3A_TRIGGER_LOW_CONFIDENCE)
        context["low_confidence"] = low_confidence
    if conflicts:
        return Pass3aDecision(
            triggered_by=PASS3A_TRIGGER_PASS1_PASS2_CONFLICT,
            context=_trim_pass3a_context(context),
        )
    if low_confidence:
        return Pass3aDecision(
            triggered_by=PASS3A_TRIGGER_LOW_CONFIDENCE,
            context=_trim_pass3a_context(context),
        )
    return None


def _pass3a_structural_conflicts(
    article: NewsArticle,
    references: list[Any],
) -> list[dict[str, Any]]:
    signals = _structural_signals(article)
    if not signals:
        return []
    conflicts: list[dict[str, Any]] = []
    market_slug = article.source.market.slug if article.source and article.source.market else None
    for reference_index, reference in enumerate(references):
        if not isinstance(reference, dict):
            continue
        reference_signals = _signals_for_reference(
            signals,
            reference=reference,
            reference_count=len(references),
        )
        for signal in reference_signals:
            conflict = _structural_signal_conflict(
                signal,
                reference=reference,
                reference_index=reference_index,
                market_slug=market_slug,
            )
            if conflict is not None:
                conflicts.append(conflict)
            if len(conflicts) >= MAX_PASS3A_CONTEXT_ITEMS:
                return conflicts
    return conflicts


def _pass3a_low_confidence_references(references: list[Any]) -> list[dict[str, Any]]:
    low_confidence: list[dict[str, Any]] = []
    for reference_index, reference in enumerate(references):
        if not isinstance(reference, dict):
            continue
        if reference.get("candidate_confidence") != "low":
            continue
        populated_fields = [
            field_name
            for field_name, payload_key in PASS3A_REFERENCE_FIELD_MAP.items()
            if reference.get(payload_key) is not None
        ]
        if not populated_fields:
            continue
        low_confidence.append(
            {
                "reference_index": reference_index,
                "candidate_name": reference.get("candidate_name"),
                "fields": populated_fields,
            }
        )
        if len(low_confidence) >= MAX_PASS3A_CONTEXT_ITEMS:
            break
    return low_confidence


def _structural_signals(article: NewsArticle) -> list[dict[str, Any]]:
    payload = article.structural_signals or {}
    signals = payload.get("signals") if isinstance(payload, dict) else None
    if not isinstance(signals, list):
        return []
    return [signal for signal in signals if isinstance(signal, dict)]


def _signals_for_reference(
    signals: list[dict[str, Any]],
    *,
    reference: dict[str, Any],
    reference_count: int,
) -> list[dict[str, Any]]:
    if reference_count == 1:
        return signals
    windows = _reference_offset_windows(reference)
    if not windows:
        return []
    selected: list[dict[str, Any]] = []
    for signal in signals:
        start = _int_or_none(signal.get("offset_start"))
        end = _int_or_none(signal.get("offset_end"))
        if start is None or end is None:
            continue
        if any(start <= window_end and end >= window_start for window_start, window_end in windows):
            selected.append(signal)
    return selected


def _reference_offset_windows(reference: dict[str, Any]) -> list[tuple[int, int]]:
    excerpts = reference.get("passage_excerpts") or []
    windows: list[tuple[int, int]] = []
    if not isinstance(excerpts, list):
        return windows
    for excerpt in excerpts:
        if not isinstance(excerpt, dict):
            continue
        start = _int_or_none(excerpt.get("offset_start"))
        end = _int_or_none(excerpt.get("offset_end"))
        if start is None or end is None:
            continue
        windows.append((max(start - 200, 0), end + 200))
    return windows


def _structural_signal_conflict(
    signal: dict[str, Any],
    *,
    reference: dict[str, Any],
    reference_index: int,
    market_slug: str | None,
) -> dict[str, Any] | None:
    extractor = signal.get("extractor")
    if extractor == "unit_count":
        return _value_conflict(
            "total_units",
            signal,
            reference.get("candidate_unit_total"),
            reference_index=reference_index,
        )
    if extractor == "status_phrase":
        metadata = signal.get("metadata") if isinstance(signal.get("metadata"), dict) else {}
        if metadata.get("signal_kind") != "pipeline_status":
            return None
        return _value_conflict(
            "pipeline_status",
            signal,
            reference.get("candidate_status_signal"),
            reference_index=reference_index,
        )
    if extractor == "delivery_phrase":
        return _value_conflict(
            "date_delivery",
            signal,
            reference.get("candidate_delivery_year_normalized"),
            reference_index=reference_index,
        )
    if extractor == "address":
        structural = signal.get("canonical")
        if not isinstance(structural, dict):
            return None
        extracted_address = reference.get("candidate_address")
        if not isinstance(extracted_address, str) or not extracted_address.strip():
            return None
        normalized = normalize_address(
            extracted_address,
            city="Los Angeles" if market_slug == "los_angeles" else None,
            state="CA",
            market=market_slug,
        )
        return _value_conflict(
            "candidate_address",
            signal,
            normalized.canonical_address,
            structural_value=structural.get("canonical_address"),
            extracted_value=extracted_address,
            reference_index=reference_index,
        )
    if extractor == "affordable_split_phrase":
        structural = signal.get("canonical")
        if not isinstance(structural, dict):
            return None
        kind = str(structural.get("kind") or "")
        count = structural.get("count")
        if count is None:
            return None
        if kind in {"affordable", "low_income", "workforce", "moderate_income"}:
            return _value_conflict(
                "affordable_units",
                signal,
                reference.get("candidate_unit_affordable"),
                structural_value=count,
                reference_index=reference_index,
            )
        if kind == "market_rate":
            return _value_conflict(
                "market_rate_units",
                signal,
                reference.get("candidate_unit_market_rate"),
                structural_value=count,
                reference_index=reference_index,
            )
    if extractor == "developer_dict":
        registry_developer_id = reference.get("registry_developer_id")
        if registry_developer_id is None:
            return None
        return _value_conflict(
            "developer",
            signal,
            str(registry_developer_id),
            reference_index=reference_index,
        )
    return None


def _value_conflict(
    field_name: str,
    signal: dict[str, Any],
    extracted_candidate: Any,
    *,
    structural_value: Any | None = None,
    extracted_value: Any | None = None,
    reference_index: int,
) -> dict[str, Any] | None:
    if extracted_candidate is None:
        return None
    resolved_structural = signal.get("canonical") if structural_value is None else structural_value
    resolved_extracted = extracted_candidate if extracted_value is None else extracted_value
    if resolved_structural is None or resolved_extracted is None:
        return None
    if _normalized_compare_value(resolved_structural) == _normalized_compare_value(
        extracted_candidate
    ):
        return None
    return {
        "reference_index": reference_index,
        "field": field_name,
        "structural_value": resolved_structural,
        "extracted_value": resolved_extracted,
        "extractor": signal.get("extractor"),
        "raw_match": signal.get("raw_match"),
        "offset_start": signal.get("offset_start"),
        "offset_end": signal.get("offset_end"),
    }


def _normalized_compare_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip().casefold()
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _trim_pass3a_context(context: dict[str, Any]) -> dict[str, Any]:
    trimmed = dict(context)
    trimmed["conflicts"] = list(trimmed.get("conflicts") or [])[:MAX_PASS3A_CONTEXT_ITEMS]
    trimmed["low_confidence"] = list(trimmed.get("low_confidence") or [])[
        :MAX_PASS3A_CONTEXT_ITEMS
    ]
    return trimmed


def _persist_extraction_api_error(
    session: Session,
    *,
    article_id: uuid.UUID,
    rendered_prompt: RenderedPrompt,
    model: str,
    error: Exception,
    now: datetime,
    pass_name: str = NewsExtractionPass.EXTRACTION.value,
    triggered_by: str = EXTRACTION_TRIGGERED_BY,
    supersedes_extraction_id: uuid.UUID | None = None,
    extra_diagnostic: dict[str, Any] | None = None,
) -> NewsExtractionRunResult:
    diagnostic = {"stage": "api_error"}
    if extra_diagnostic:
        diagnostic.update(extra_diagnostic)
    extraction = NewsExtraction(
        article_id=article_id,
        pass_name=pass_name,
        triggered_by=triggered_by,
        supersedes_extraction_id=supersedes_extraction_id,
        prompt_id=rendered_prompt.prompt_id,
        prompt_version=rendered_prompt.prompt_version,
        prompt_hash=rendered_prompt.prompt_hash,
        model=model,
        input_tokens_uncached=0,
        input_tokens_cache_creation=0,
        input_tokens_cached=0,
        output_tokens=0,
        cost_usd=Decimal("0"),
        latency_ms=0,
        output_json=None,
        raw_response_text=None,
        parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
        parse_error_text=str(error),
        diagnostic=diagnostic,
        created_at=now,
    )
    session.add(extraction)
    session.flush()
    return NewsExtractionRunResult(
        article_id=article_id,
        extraction_id=extraction.id,
        relevance=None,
        reference_count=0,
        parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
        triggered_by=triggered_by,
    )


def _reference_from_payload(
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    reference_index: int,
    payload: dict[str, Any],
) -> NewsProjectReference:
    return NewsProjectReference(
        extraction_id=extraction_id,
        article_id=article_id,
        reference_index=reference_index,
        candidate_name=payload.get("candidate_name"),
        candidate_address=payload.get("candidate_address"),
        candidate_developer=payload.get("candidate_developer"),
        candidate_unit_total=payload.get("candidate_unit_total"),
        candidate_unit_affordable=payload.get("candidate_unit_affordable"),
        candidate_unit_market_rate=payload.get("candidate_unit_market_rate"),
        candidate_product_type=payload.get("candidate_product_type"),
        candidate_age_restriction=payload.get("candidate_age_restriction"),
        candidate_status_signal=payload.get("candidate_status_signal"),
        candidate_delivery_year_text=payload.get("candidate_delivery_year_text"),
        candidate_delivery_year_normalized=_date_or_none(
            payload.get("candidate_delivery_year_normalized")
        ),
        candidate_signal_flags=payload.get("candidate_signal_flags") or {},
        candidate_identifiers=payload.get("candidate_identifiers") or {
            "case_number": [],
            "permit_number": [],
            "apn": [],
        },
        candidate_neighborhood=payload.get("candidate_neighborhood"),
        candidate_lat=payload.get("candidate_lat"),
        candidate_lng=payload.get("candidate_lng"),
        candidate_confidence=payload.get("candidate_confidence") or "low",
        passage_excerpts=payload.get("passage_excerpts") or [],
        match_status=NewsMatchStatus.PENDING.value,
    )


def _normalized_extraction_payload(
    parsed: ExtractionOutputPayload,
    *,
    active_signal_flags: set[str],
) -> tuple[dict[str, Any], set[str]]:
    payload = parsed.model_dump(mode="json")
    unknown_signal_flags: set[str] = set()
    for reference in payload["project_references"]:
        flags = reference.get("candidate_signal_flags") or {}
        normalized_flags: dict[str, bool] = {}
        for flag_key, enabled in flags.items():
            if not enabled:
                continue
            if active_signal_flags and flag_key not in active_signal_flags:
                unknown_signal_flags.add(flag_key)
                continue
            normalized_flags[flag_key] = True
        reference["candidate_signal_flags"] = normalized_flags
    if unknown_signal_flags:
        diagnostic = payload.setdefault("diagnostic", {})
        diagnostic["unknown_signal_flags"] = sorted(unknown_signal_flags)
    return payload, unknown_signal_flags


def _article_skip_reason(article: NewsArticle) -> str | None:
    if article.fetch_status != NewsFetchStatus.FETCHED.value or not article.body_text:
        return "article_not_fetched"
    if article.triage_status != NewsTriageStatus.RELEVANT.value:
        return "triage_not_relevant"
    if article.current_extraction_id is not None:
        return "already_extracted"
    return None


def _active_signal_flag_keys(session: Session) -> set[str]:
    return set(
        session.execute(
            select(NewsSignalFlag.flag_key).where(
                NewsSignalFlag.active.is_(True),
                NewsSignalFlag.retired_at.is_(None),
            )
        ).scalars()
    )


def _raise_missing_api_key_alert(session: Session, *, now: datetime) -> None:
    statement = (
        insert(SystemAlert)
        .values(
            alert_key="news_anthropic_api_key_missing",
            severity="warning",
            scope={"component": "news_extraction"},
            message="ANTHROPIC_API_KEY is not configured; news extraction is skipped.",
            detail={"skipped_reason": "no_api_key"},
            raised_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                SystemAlert.alert_key,
                text("COALESCE(scope::text, '{}')"),
            ],
            index_where=text("cleared_at IS NULL"),
            set_={
                "severity": "warning",
                "message": "ANTHROPIC_API_KEY is not configured; news extraction is skipped.",
                "detail": {"skipped_reason": "no_api_key"},
                "last_seen_at": now,
            },
        )
    )
    session.execute(statement)


def _date_or_none(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _strip_json_fence(raw_text: str) -> str:
    text_value = raw_text.strip()
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text_value,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return text_value
