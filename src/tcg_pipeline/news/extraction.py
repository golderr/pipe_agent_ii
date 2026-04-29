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
from tcg_pipeline.news.prompts import RenderedPrompt, render_extraction_prompt
from tcg_pipeline.settings import Settings, get_settings

EXTRACTION_TRIGGERED_BY = "initial"
EXTRACTION_ESTIMATED_COST_USD = Decimal("0.75")
EXTRACTION_TEMPERATURE = 0
EXTRACTION_MAX_TOKENS = 2500
EXTRACTION_TOOL_NAME = "emit_project_extraction"


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
    return result


def build_anthropic_extraction_client(settings: Settings) -> AnthropicExtractionClient:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for news extraction.")
    return AnthropicExtractionClient(
        api_key=settings.anthropic_api_key,
        model=settings.news_extract_model,
        max_tokens=settings.news_extract_max_tokens,
    )


def persist_extraction_response(
    session: Session,
    *,
    article_id: uuid.UUID,
    rendered_prompt: RenderedPrompt,
    llm_response: ExtractionLLMResponse,
    reserved_cost_usd: Decimal = Decimal("0"),
    now: datetime | None = None,
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
        pass_name=NewsExtractionPass.EXTRACTION.value,
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
    if parsed.unknown_signal_flags:
        diagnostic["unknown_signal_flags"] = list(parsed.unknown_signal_flags)
    extraction = NewsExtraction(
        article_id=article_id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by=EXTRACTION_TRIGGERED_BY,
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


def _persist_extraction_api_error(
    session: Session,
    *,
    article_id: uuid.UUID,
    rendered_prompt: RenderedPrompt,
    model: str,
    error: Exception,
    now: datetime,
) -> NewsExtractionRunResult:
    extraction = NewsExtraction(
        article_id=article_id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by=EXTRACTION_TRIGGERED_BY,
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
        diagnostic={"stage": "api_error"},
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
