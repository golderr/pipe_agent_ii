from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsProjectReference,
    NewsSemanticInterpretation,
    SystemAlert,
)
from tcg_pipeline.news.costs import (
    record_llm_cost,
    release_llm_cost_reservation,
    reserve_llm_cost,
)
from tcg_pipeline.news.llm import (
    DEFAULT_EXTRACTION_MODEL,
    LLM_PROVIDER_ANTHROPIC,
    OPENAI_COMPATIBLE_PROVIDERS,
    LLMUsage,
    OpenAIResponsesJSONClient,
    anthropic_usage,
    calculate_llm_cost_usd,
    create_anthropic_message,
    normalize_llm_provider,
    pricing_for_model,
    provider_api_key,
    provider_base_url,
)
from tcg_pipeline.semantic.constants import NEWS_SEMANTIC_CAPABILITY
from tcg_pipeline.semantic.jurisdiction import (
    default_jurisdiction_policy,
    load_jurisdiction_policy,
)
from tcg_pipeline.semantic.news.prompting import (
    assemble_interpret_system_prompt,
    load_interpret_schema,
)
from tcg_pipeline.semantic.reason_codes import ReasonCodeRegistry
from tcg_pipeline.semantic.types import Confidence, PassageAnchor, SemanticInterpretation
from tcg_pipeline.settings import Settings, get_settings

INTERPRET_TEMPERATURE = 0
INTERPRET_TOOL_NAME = "emit_semantic_interpretations"
INTERPRET_SCHEMA_NAME = "news_semantic_interpretation"
INTERPRET_RETRY_PROMPT_ID = "interpret_retry_v1"
INTERPRET_RETRY_PROMPT_VERSION = "v1"
# Reservation ceiling for one Pass 2c call; actual billing is recorded from token usage.
INTERPRET_ESTIMATED_COST_USD = Decimal("0.10")
INTERPRET_RETRY_ESTIMATED_COST_USD = Decimal("0.20")
MAX_ARTICLE_BODY_CHARS = 30_000
PROMPT_VERSION_RE = re.compile(r".*_(v\d+)$")
TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "length", "max_output_tokens"})
REFUSAL_STOP_REASONS = frozenset({"refusal"})
SEMANTIC_PARSE_ALERT_KEY = "news_semantic_parse_failed"
SEMANTIC_PARSE_ALERT_STATUSES = frozenset(
    {
        NewsExtractionParseStatus.TRUNCATED.value,
        NewsExtractionParseStatus.REFUSED.value,
        NewsExtractionParseStatus.PARSE_ERROR.value,
        NewsExtractionParseStatus.SCHEMA_INVALID.value,
    }
)
SEMANTIC_RETRY_STATUSES = frozenset({NewsExtractionParseStatus.TRUNCATED.value})


@dataclass(frozen=True, slots=True)
class RenderedInterpretPrompt:
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    capability_key: str
    system_text: str
    user_text: str
    schema: dict[str, Any]
    reason_code_registry: ReasonCodeRegistry
    system_blocks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticLLMResponse:
    payload: dict[str, Any] | None
    text: str
    model: str
    provider: str
    usage: LLMUsage
    latency_ms: int
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedSemanticResponse:
    payload: dict[str, Any] | None
    interpretations: tuple[SemanticInterpretation, ...]
    parse_status: str
    parse_error_text: str | None
    diagnostic: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NewsSemanticInterpretationRunResult:
    article_id: uuid.UUID
    extraction_id: uuid.UUID
    semantic_interpretation_id: uuid.UUID | None
    interpretation_count: int
    parse_status: str | None
    skipped_reason: str | None = None
    error_text: str | None = None


class Pass2cClient(Protocol):
    model: str
    provider: str

    def interpret(self, prompt: RenderedInterpretPrompt) -> SemanticLLMResponse: ...


class AnthropicPass2cClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EXTRACTION_MODEL,
        max_tokens: int,
    ) -> None:
        self.model = model
        self.provider = LLM_PROVIDER_ANTHROPIC
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def interpret(self, prompt: RenderedInterpretPrompt) -> SemanticLLMResponse:
        started_at = time.perf_counter()
        response = create_anthropic_message(
            self._client,
            model=self.model,
            max_tokens=self._max_tokens,
            temperature=INTERPRET_TEMPERATURE,
            system=_cacheable_system_blocks(prompt),
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt.user_text}],
                }
            ],
            tools=[
                {
                    "name": INTERPRET_TOOL_NAME,
                    "description": "Emit canonical TCG semantic interpretations.",
                    "input_schema": prompt.schema,
                }
            ],
            tool_choice={"type": "tool", "name": INTERPRET_TOOL_NAME},
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        payload: dict[str, Any] | None = None
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use" and getattr(block, "name", None) == INTERPRET_TOOL_NAME:
                block_input = getattr(block, "input", None)
                if isinstance(block_input, dict):
                    payload = block_input
            elif block_type == "text":
                text_parts.append(getattr(block, "text", ""))
        raw_text = (
            json.dumps(payload, sort_keys=True) if payload is not None else "\n".join(text_parts)
        )
        return SemanticLLMResponse(
            payload=payload,
            text=raw_text,
            model=response.model,
            provider=self.provider,
            usage=anthropic_usage(response.usage),
            latency_ms=latency_ms,
            stop_reason=response.stop_reason,
        )


class OpenAIPass2cClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider: str,
        base_url: str,
        max_tokens: int,
        timeout_seconds: float = 90.0,
    ) -> None:
        self.model = model
        self.provider = normalize_llm_provider(provider)
        self._client = OpenAIResponsesJSONClient(
            api_key=api_key,
            model=model,
            provider=self.provider,
            base_url=base_url,
            max_output_tokens=max_tokens,
            temperature=INTERPRET_TEMPERATURE,
            timeout_seconds=timeout_seconds,
        )

    def interpret(self, prompt: RenderedInterpretPrompt) -> SemanticLLMResponse:
        response = self._client.create_json_response(
            system_text=prompt.system_text,
            user_text=prompt.user_text,
            schema=prompt.schema,
            schema_name=INTERPRET_SCHEMA_NAME,
        )
        return SemanticLLMResponse(
            payload=response.payload,
            text=response.text,
            model=response.model,
            provider=response.provider,
            usage=response.usage,
            latency_ms=response.latency_ms,
            stop_reason=response.stop_reason,
        )


class PassageAnchorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    offset_start: int | None = Field(default=None, ge=0)
    offset_end: int | None = Field(default=None, ge=0)
    field_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_offsets(self) -> PassageAnchorPayload:
        if (
            self.offset_start is not None
            and self.offset_end is not None
            and self.offset_end < self.offset_start
        ):
            raise ValueError("offset_end must be greater than or equal to offset_start")
        return self


class SemanticInterpretationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    canonical_value: Any | None = None
    confidence: Confidence
    reason_code: str
    signal_flags: dict[str, Any] = Field(default_factory=dict)
    source_anchors: list[PassageAnchorPayload] = Field(default_factory=list)
    requires_corroboration: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticOutputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interpretations: list[SemanticInterpretationPayload] = Field(default_factory=list)
    diagnostic: dict[str, Any] = Field(default_factory=dict)


def build_pass2c_client(
    settings: Settings,
    *,
    max_tokens_override: int | None = None,
) -> Pass2cClient:
    provider = normalize_llm_provider(settings.news_semantic_provider)
    max_tokens = max_tokens_override or settings.news_semantic_max_tokens
    if provider == LLM_PROVIDER_ANTHROPIC:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for news semantic interpretation.")
        return AnthropicPass2cClient(
            api_key=settings.anthropic_api_key,
            model=settings.news_semantic_model,
            max_tokens=max_tokens,
        )
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        api_key = provider_api_key(settings, provider)
        if not api_key:
            raise RuntimeError(f"{provider} API key is required for news semantic interpretation.")
        return OpenAIPass2cClient(
            api_key=api_key,
            model=settings.news_semantic_model,
            provider=provider,
            base_url=provider_base_url(settings, provider),
            max_tokens=max_tokens,
            timeout_seconds=settings.news_llm_timeout_seconds,
        )
    raise RuntimeError(f"Unsupported news semantic provider: {provider}")


def render_interpret_prompt(
    session: Session,
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    references: tuple[NewsProjectReference, ...] | None = None,
    project_context: tuple[dict[str, Any], ...] | None = None,
    recent_evidence: tuple[dict[str, Any], ...] | None = None,
) -> RenderedInterpretPrompt:
    source = article.source
    market_slug = (
        source.market.slug
        if source is not None and source.market is not None
        else "unscoped"
    )
    assembled = assemble_interpret_system_prompt(market_slug)
    schema = load_interpret_schema()
    current_references = references or _references_for_extraction(session, extraction.id)
    user_payload = {
        "article": _article_payload(article),
        "source_profile": "news_v1",
        "market_slug": market_slug,
        "fallback_jurisdiction_policy": _jurisdiction_policy_payload(article),
        "pass2b_extraction": _extraction_payload(extraction),
        "pass2b_references": [
            _reference_payload(reference) for reference in current_references
        ],
        "project_context": list(project_context or ()),
        "recent_evidence": list(recent_evidence or ()),
        "instructions": (
            "Return interpretations for article-supported canonical TCG fields. "
            "Use source anchors and registry reason codes."
        ),
    }
    user_text = json.dumps(serialize_json(user_payload), ensure_ascii=False, sort_keys=True)
    prompt_hash = _prompt_hash(assembled.system_text, user_text)
    return RenderedInterpretPrompt(
        prompt_id=assembled.prompt_id,
        prompt_version=_prompt_version(assembled.prompt_id),
        prompt_hash=prompt_hash,
        capability_key=NEWS_SEMANTIC_CAPABILITY,
        system_text=assembled.system_text,
        user_text=user_text,
        schema=schema,
        reason_code_registry=assembled.reason_code_registry,
        system_blocks=assembled.system_blocks,
    )


def run_news_semantic_interpretation_for_extraction(
    extraction_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    client: Pass2cClient | None = None,
    session_factory: sessionmaker[Session] | None = None,
    project_context: tuple[dict[str, Any], ...] | None = None,
    recent_evidence: tuple[dict[str, Any], ...] | None = None,
    now: datetime | None = None,
) -> NewsSemanticInterpretationRunResult:
    from tcg_pipeline.db.connection import get_session_factory

    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or get_session_factory()
    current = now or datetime.now(UTC)

    with resolved_session_factory() as session:
        extraction = session.get(NewsExtraction, extraction_id)
        if extraction is None:
            raise RuntimeError("Pass 2c references a missing news extraction.")
        article = session.get(NewsArticle, extraction.article_id)
        if article is None:
            raise RuntimeError("Pass 2c references a missing news article.")
        if extraction.parse_status != NewsExtractionParseStatus.OK.value:
            return NewsSemanticInterpretationRunResult(
                article_id=article.id,
                extraction_id=extraction.id,
                semantic_interpretation_id=None,
                interpretation_count=0,
                parse_status=None,
                skipped_reason="extraction_not_ok",
            )
        prompt = render_interpret_prompt(
            session,
            article=article,
            extraction=extraction,
            project_context=project_context,
            recent_evidence=recent_evidence,
        )

    provider = normalize_llm_provider(resolved_settings.news_semantic_provider)
    if client is None and not provider_api_key(resolved_settings, provider):
        return NewsSemanticInterpretationRunResult(
            article_id=article.id,
            extraction_id=extraction.id,
            semantic_interpretation_id=None,
            interpretation_count=0,
            parse_status=None,
            skipped_reason="no_api_key",
        )

    pass2c_client = client or build_pass2c_client(resolved_settings)
    pricing_for_model(pass2c_client.model)
    result = _run_pass2c_call(
        session_factory=resolved_session_factory,
        article_id=article.id,
        extraction_id=extraction.id,
        prompt=prompt,
        pass2c_client=pass2c_client,
        estimated_cost_usd=INTERPRET_ESTIMATED_COST_USD,
        now=current,
    )
    if result.parse_status not in SEMANTIC_RETRY_STATUSES:
        return result

    retry_prompt = replace(
        prompt,
        prompt_id=INTERPRET_RETRY_PROMPT_ID,
        prompt_version=INTERPRET_RETRY_PROMPT_VERSION,
    )
    # Injected clients cannot be rebuilt with a larger max-token ceiling, so tests
    # and harnesses that pass a custom client get the same client with retry audit
    # metadata. Production client construction applies the retry token bump.
    retry_client = client or build_pass2c_client(
        resolved_settings,
        max_tokens_override=resolved_settings.news_semantic_retry_max_tokens,
    )
    pricing_for_model(retry_client.model)
    return _run_pass2c_call(
        session_factory=resolved_session_factory,
        article_id=article.id,
        extraction_id=extraction.id,
        prompt=retry_prompt,
        pass2c_client=retry_client,
        estimated_cost_usd=INTERPRET_RETRY_ESTIMATED_COST_USD,
        diagnostic_extra={
            "retry_reason": result.parse_status,
            "retry_of_semantic_interpretation_id": (
                str(result.semantic_interpretation_id)
                if result.semantic_interpretation_id is not None
                else None
            ),
            "initial_error_text": result.error_text,
            "max_tokens": resolved_settings.news_semantic_retry_max_tokens,
        },
        now=_retry_timestamp_after(current),
    )


def _run_pass2c_call(
    *,
    session_factory: sessionmaker[Session],
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    prompt: RenderedInterpretPrompt,
    pass2c_client: Pass2cClient,
    estimated_cost_usd: Decimal,
    diagnostic_extra: dict[str, Any] | None = None,
    now: datetime,
) -> NewsSemanticInterpretationRunResult:
    with session_factory() as session:
        reservation = reserve_llm_cost(
            session,
            pass_name=NEWS_SEMANTIC_CAPABILITY,
            model=pass2c_client.model,
            provider=pass2c_client.provider,
            estimated_cost_usd=estimated_cost_usd,
            now=now,
        )
        session.commit()
    if reservation is None:
        return NewsSemanticInterpretationRunResult(
            article_id=article_id,
            extraction_id=extraction_id,
            semantic_interpretation_id=None,
            interpretation_count=0,
            parse_status=None,
            skipped_reason="cost_cap",
        )

    try:
        llm_response = pass2c_client.interpret(prompt)
    except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary.
        with session_factory() as session:
            release_llm_cost_reservation(
                session,
                reserved_cost_usd=estimated_cost_usd,
                now=now,
            )
            row = persist_semantic_api_error(
                session,
                article_id=article_id,
                extraction_id=extraction_id,
                rendered_prompt=prompt,
                model=pass2c_client.model,
                provider=pass2c_client.provider,
                error=exc,
                diagnostic_extra=diagnostic_extra,
                now=now,
            )
            session.commit()
        return NewsSemanticInterpretationRunResult(
            article_id=article_id,
            extraction_id=extraction_id,
            semantic_interpretation_id=row.id,
            interpretation_count=0,
            parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
            error_text=str(exc),
        )

    with session_factory() as session:
        result = persist_semantic_response(
            session,
            article_id=article_id,
            extraction_id=extraction_id,
            rendered_prompt=prompt,
            llm_response=llm_response,
            reserved_cost_usd=estimated_cost_usd,
            diagnostic_extra=diagnostic_extra,
            now=now,
        )
        session.commit()
        return result


def _retry_timestamp_after(initial_timestamp: datetime) -> datetime:
    current = datetime.now(UTC)
    if current <= initial_timestamp:
        return initial_timestamp + timedelta(microseconds=1)
    return current


def persist_semantic_response(
    session: Session,
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    rendered_prompt: RenderedInterpretPrompt,
    llm_response: SemanticLLMResponse,
    reserved_cost_usd: Decimal = Decimal("0"),
    diagnostic_extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> NewsSemanticInterpretationRunResult:
    current = now or datetime.now(UTC)
    parsed = parse_semantic_response(
        llm_response.payload,
        raw_text=llm_response.text,
        stop_reason=llm_response.stop_reason,
        registry=rendered_prompt.reason_code_registry,
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
        pass_name=NEWS_SEMANTIC_CAPABILITY,
        model=llm_response.model,
        provider=llm_response.provider,
        input_tokens_uncached=llm_response.usage.input_tokens_uncached,
        input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
        input_tokens_cached=llm_response.usage.input_tokens_cached,
        output_tokens=llm_response.usage.output_tokens,
        cost_usd=cost_usd,
        reserved_cost_usd=reserved_cost_usd,
        now=current,
    )
    diagnostic = {
        "stop_reason": llm_response.stop_reason,
        "capability_key": rendered_prompt.capability_key,
    }
    diagnostic.update(parsed.diagnostic)
    if diagnostic_extra:
        diagnostic.update(serialize_json(diagnostic_extra))
    row = NewsSemanticInterpretation(
        article_id=article_id,
        extraction_id=extraction_id,
        prompt_id=rendered_prompt.prompt_id,
        prompt_version=rendered_prompt.prompt_version,
        prompt_hash=rendered_prompt.prompt_hash,
        model=llm_response.model,
        model_provider=llm_response.provider,
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
        created_at=current,
    )
    session.add(row)
    session.flush()
    _sync_semantic_parse_alert(
        session,
        article_id=article_id,
        extraction_id=extraction_id,
        row=row,
        parse_status=parsed.parse_status,
        parse_error_text=parsed.parse_error_text,
        now=current,
    )
    return NewsSemanticInterpretationRunResult(
        article_id=article_id,
        extraction_id=extraction_id,
        semantic_interpretation_id=row.id,
        interpretation_count=len(parsed.interpretations),
        parse_status=parsed.parse_status,
        error_text=parsed.parse_error_text,
    )


def persist_semantic_api_error(
    session: Session,
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    rendered_prompt: RenderedInterpretPrompt,
    model: str,
    provider: str,
    error: Exception,
    diagnostic_extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> NewsSemanticInterpretation:
    current = now or datetime.now(UTC)
    diagnostic = {
        "capability_key": rendered_prompt.capability_key,
        "error_type": type(error).__name__,
    }
    if diagnostic_extra:
        diagnostic.update(serialize_json(diagnostic_extra))
    row = NewsSemanticInterpretation(
        article_id=article_id,
        extraction_id=extraction_id,
        prompt_id=rendered_prompt.prompt_id,
        prompt_version=rendered_prompt.prompt_version,
        prompt_hash=rendered_prompt.prompt_hash,
        model=model,
        model_provider=provider,
        output_json=None,
        raw_response_text=None,
        parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
        parse_error_text=str(error),
        diagnostic=diagnostic,
        created_at=current,
    )
    session.add(row)
    session.flush()
    _sync_semantic_parse_alert(
        session,
        article_id=article_id,
        extraction_id=extraction_id,
        row=row,
        parse_status=row.parse_status,
        parse_error_text=row.parse_error_text,
        now=current,
    )
    return row


def _sync_semantic_parse_alert(
    session: Session,
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    row: NewsSemanticInterpretation,
    parse_status: str,
    parse_error_text: str | None,
    now: datetime,
) -> None:
    if parse_status in SEMANTIC_PARSE_ALERT_STATUSES:
        _raise_semantic_parse_alert(
            session,
            article_id=article_id,
            extraction_id=extraction_id,
            row=row,
            parse_status=parse_status,
            parse_error_text=parse_error_text,
            now=now,
        )
        return
    _clear_semantic_parse_alert(
        session,
        article_id=article_id,
        extraction_id=extraction_id,
        now=now,
        cleared_reason="news_semantic_interpretation_succeeded",
    )


def _raise_semantic_parse_alert(
    session: Session,
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    row: NewsSemanticInterpretation,
    parse_status: str,
    parse_error_text: str | None,
    now: datetime,
) -> None:
    scope = _semantic_parse_alert_scope(
        article_id=article_id,
        extraction_id=extraction_id,
    )
    detail = {
        "article_id": str(article_id),
        "extraction_id": str(extraction_id),
        "semantic_interpretation_id": str(row.id),
        "parse_status": parse_status,
        "parse_error_text": parse_error_text,
        "prompt_id": row.prompt_id,
        "prompt_version": row.prompt_version,
        "model": row.model,
        "provider": row.model_provider,
        "output_tokens": row.output_tokens,
        "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
        "diagnostic": row.diagnostic or {},
    }
    message = (
        "News semantic interpretation did not produce usable structured output "
        f"for extraction {extraction_id}; parse_status={parse_status}."
    )
    statement = (
        insert(SystemAlert)
        .values(
            alert_key=SEMANTIC_PARSE_ALERT_KEY,
            severity="warning",
            scope=scope,
            message=message,
            detail=serialize_json(detail),
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
                "message": message,
                "detail": serialize_json(detail),
                "last_seen_at": now,
            },
        )
    )
    session.execute(statement)


def _clear_semantic_parse_alert(
    session: Session,
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
    now: datetime,
    cleared_reason: str,
) -> None:
    session.execute(
        update(SystemAlert)
        .where(
            SystemAlert.alert_key == SEMANTIC_PARSE_ALERT_KEY,
            SystemAlert.scope
            == _semantic_parse_alert_scope(
                article_id=article_id,
                extraction_id=extraction_id,
            ),
            SystemAlert.cleared_at.is_(None),
        )
        .values(
            cleared_at=now,
            cleared_reason=cleared_reason,
            last_seen_at=now,
        )
    )


def _semantic_parse_alert_scope(
    *,
    article_id: uuid.UUID,
    extraction_id: uuid.UUID,
) -> dict[str, str]:
    return {
        "article_id": str(article_id),
        "extraction_id": str(extraction_id),
    }


def parse_semantic_response(
    payload: dict[str, Any] | None,
    *,
    raw_text: str,
    stop_reason: str | None = None,
    registry: ReasonCodeRegistry,
) -> ParsedSemanticResponse:
    if stop_reason in TRUNCATED_STOP_REASONS:
        return ParsedSemanticResponse(
            payload=None,
            interpretations=(),
            parse_status=NewsExtractionParseStatus.TRUNCATED.value,
            parse_error_text=stop_reason,
            diagnostic={},
        )
    if stop_reason in REFUSAL_STOP_REASONS:
        return ParsedSemanticResponse(
            payload=None,
            interpretations=(),
            parse_status=NewsExtractionParseStatus.REFUSED.value,
            parse_error_text=stop_reason,
            diagnostic={},
        )
    candidate_payload = payload
    parser_recovered_root_array = False
    if candidate_payload is None:
        try:
            loaded = json.loads(_strip_json_fence(raw_text).strip())
        except json.JSONDecodeError as exc:
            return ParsedSemanticResponse(
                payload=None,
                interpretations=(),
                parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
                parse_error_text=str(exc),
                diagnostic={},
            )
        if isinstance(loaded, list):
            candidate_payload = {"interpretations": loaded, "diagnostic": {}}
            parser_recovered_root_array = True
        elif isinstance(loaded, dict):
            candidate_payload = loaded
        else:
            return ParsedSemanticResponse(
                payload={"value": loaded},
                interpretations=(),
                parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
                parse_error_text="Semantic interpretation response root must be an object or list.",
                diagnostic={},
            )
    try:
        parsed = SemanticOutputPayload.model_validate(candidate_payload)
        interpretations = _semantic_interpretations_from_output(parsed, registry=registry)
    except (ValidationError, ValueError) as exc:
        return ParsedSemanticResponse(
            payload=candidate_payload,
            interpretations=(),
            parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
            parse_error_text=str(exc),
            diagnostic={},
        )
    diagnostic = serialize_json(parsed.diagnostic)
    if parser_recovered_root_array:
        diagnostic["parser_recovered_root_array"] = True
    normalized_payload = {
        "interpretations": [
            _interpretation_to_payload(interpretation) for interpretation in interpretations
        ],
        "diagnostic": diagnostic,
    }
    return ParsedSemanticResponse(
        payload=normalized_payload,
        interpretations=interpretations,
        parse_status=NewsExtractionParseStatus.OK.value,
        parse_error_text=None,
        diagnostic=diagnostic,
    )


def load_current_semantic_interpretation_row(
    session: Session,
    extraction_id: uuid.UUID,
) -> NewsSemanticInterpretation | None:
    return (
        session.execute(
            select(NewsSemanticInterpretation)
            .where(
                NewsSemanticInterpretation.extraction_id == extraction_id,
                NewsSemanticInterpretation.parse_status == NewsExtractionParseStatus.OK.value,
            )
            .order_by(
                NewsSemanticInterpretation.created_at.desc(),
                NewsSemanticInterpretation.id.desc(),
            )
            .limit(1)
        )
        .scalars()
        .one_or_none()
    )


def load_current_semantic_interpretations(
    session: Session,
    extraction_id: uuid.UUID,
    *,
    registry: ReasonCodeRegistry,
) -> tuple[SemanticInterpretation, ...]:
    row = load_current_semantic_interpretation_row(session, extraction_id)
    if row is None:
        return ()
    return semantic_interpretations_from_output_json(row.output_json, registry=registry)


def semantic_interpretations_from_output_json(
    output_json: Any,
    *,
    registry: ReasonCodeRegistry,
) -> tuple[SemanticInterpretation, ...]:
    if isinstance(output_json, list):
        payload = {"interpretations": output_json, "diagnostic": {}}
    elif isinstance(output_json, dict):
        payload = output_json
    else:
        return ()
    parsed = SemanticOutputPayload.model_validate(payload)
    return _semantic_interpretations_from_output(parsed, registry=registry)


def _cacheable_system_blocks(prompt: RenderedInterpretPrompt) -> list[dict[str, object]]:
    blocks = prompt.system_blocks or (prompt.system_text,)
    return [
        {
            "type": "text",
            "text": block,
            "cache_control": {"type": "ephemeral"},
        }
        for block in blocks
    ]


def _references_for_extraction(
    session: Session,
    extraction_id: uuid.UUID,
) -> tuple[NewsProjectReference, ...]:
    return tuple(
        session.execute(
            select(NewsProjectReference)
            .where(NewsProjectReference.extraction_id == extraction_id)
            .order_by(
                NewsProjectReference.reference_index.asc(),
                NewsProjectReference.id.asc(),
            )
        )
        .scalars()
        .all()
    )


def _article_payload(article: NewsArticle) -> dict[str, Any]:
    source = article.source
    body_text = article.body_text or ""
    return {
        "article_id": str(article.id),
        "title": article.title,
        "url": article.url_canonical,
        "source_slug": source.slug if source is not None else None,
        "source_name": source.name if source is not None else None,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "fetched_at": article.fetched_at.isoformat() if article.fetched_at else None,
        "byline_author": article.byline_author,
        "publication_section": article.publication_section,
        "structural_signals": article.structural_signals or {},
        "body_text": _trim_text(body_text, MAX_ARTICLE_BODY_CHARS),
        "body_text_truncated": len(body_text) > MAX_ARTICLE_BODY_CHARS,
    }


def _jurisdiction_policy_payload(article: NewsArticle) -> dict[str, Any]:
    source = article.source
    jurisdiction = source.jurisdiction if source is not None else None
    policy = (
        load_jurisdiction_policy(jurisdiction.slug)
        if jurisdiction is not None
        else default_jurisdiction_policy()
    )
    return {
        "jurisdiction_slug": jurisdiction.slug if jurisdiction is not None else None,
        "jurisdiction_name": jurisdiction.name if jurisdiction is not None else None,
        "policy_scope": "article_source_fallback",
        **policy.as_prompt_payload(),
    }


def _extraction_payload(extraction: NewsExtraction) -> dict[str, Any]:
    return {
        "extraction_id": str(extraction.id),
        "pass": extraction.pass_name,
        "triggered_by": extraction.triggered_by,
        "prompt_id": extraction.prompt_id,
        "prompt_version": extraction.prompt_version,
        "parse_status": extraction.parse_status,
        "output_json": extraction.output_json or {},
    }


def _reference_payload(reference: NewsProjectReference) -> dict[str, Any]:
    return {
        "reference_id": str(reference.id),
        "reference_index": reference.reference_index,
        "candidate_name": reference.candidate_name,
        "candidate_address": reference.candidate_address,
        "candidate_city": reference.candidate_city,
        "candidate_developer": reference.candidate_developer,
        "candidate_unit_total": reference.candidate_unit_total,
        "candidate_unit_affordable": reference.candidate_unit_affordable,
        "candidate_unit_market_rate": reference.candidate_unit_market_rate,
        "candidate_unit_workforce": reference.candidate_unit_workforce,
        "candidate_product_type": reference.candidate_product_type,
        "candidate_age_restriction": reference.candidate_age_restriction,
        "candidate_status_signal": reference.candidate_status_signal,
        "candidate_delivery_year_text": reference.candidate_delivery_year_text,
        "candidate_delivery_year_normalized": reference.candidate_delivery_year_normalized,
        "candidate_signal_flags": reference.candidate_signal_flags or {},
        "candidate_identifiers": reference.candidate_identifiers or {},
        "candidate_neighborhood": reference.candidate_neighborhood,
        "candidate_lat": reference.candidate_lat,
        "candidate_lng": reference.candidate_lng,
        "candidate_confidence": reference.candidate_confidence,
        "passage_excerpts": reference.passage_excerpts or [],
        "match_status": reference.match_status,
        "match_project_id": str(reference.matched_project_id)
        if reference.matched_project_id
        else None,
    }


def _semantic_interpretations_from_output(
    parsed: SemanticOutputPayload,
    *,
    registry: ReasonCodeRegistry,
) -> tuple[SemanticInterpretation, ...]:
    interpretations: list[SemanticInterpretation] = []
    for item in parsed.interpretations:
        reason = registry.by_code.get(item.reason_code)
        if reason is None:
            raise ValueError(f"Unknown semantic reason_code: {item.reason_code}")
        if reason.source_profile != "news_v1":
            raise ValueError(f"Reason code is not valid for news_v1: {item.reason_code}")
        if reason.field_name != item.field_name:
            raise ValueError(
                f"Reason code {item.reason_code} is registered for "
                f"{reason.field_name}, not {item.field_name}"
            )
        interpretations.append(
            SemanticInterpretation(
                field_name=item.field_name,
                canonical_value=serialize_json(item.canonical_value),
                confidence=item.confidence,
                reason_code=item.reason_code,
                signal_flags=serialize_json(item.signal_flags),
                source_anchors=tuple(
                    PassageAnchor(
                        text=anchor.text,
                        offset_start=anchor.offset_start,
                        offset_end=anchor.offset_end,
                        field_name=anchor.field_name,
                        metadata=serialize_json(anchor.metadata),
                    )
                    for anchor in item.source_anchors
                ),
                requires_corroboration=(
                    item.requires_corroboration or reason.requires_corroboration
                ),
                metadata=serialize_json(item.metadata),
            )
        )
    return tuple(interpretations)


def _interpretation_to_payload(interpretation: SemanticInterpretation) -> dict[str, Any]:
    return {
        "field_name": interpretation.field_name,
        "canonical_value": serialize_json(interpretation.canonical_value),
        "confidence": interpretation.confidence,
        "reason_code": interpretation.reason_code,
        "signal_flags": serialize_json(interpretation.signal_flags),
        "source_anchors": [
            {
                "text": anchor.text,
                "offset_start": anchor.offset_start,
                "offset_end": anchor.offset_end,
                "field_name": anchor.field_name,
                "metadata": serialize_json(anchor.metadata),
            }
            for anchor in interpretation.source_anchors
        ],
        "requires_corroboration": interpretation.requires_corroboration,
        "metadata": serialize_json(interpretation.metadata),
    }


def _prompt_hash(system_text: str, user_text: str) -> str:
    return hashlib.sha256((system_text + "\n\n" + user_text).encode("utf-8")).hexdigest()


def _prompt_version(prompt_id: str) -> str:
    match = PROMPT_VERSION_RE.fullmatch(prompt_id)
    if match is None:
        raise ValueError(f"Interpret prompt_id must end with version suffix: {prompt_id}")
    return match.group(1)


def _trim_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    return match.group(1) if match else stripped
