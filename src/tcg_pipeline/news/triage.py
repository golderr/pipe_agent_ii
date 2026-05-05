from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import anthropic
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsTriageStatus,
    SystemAlert,
)
from tcg_pipeline.news.costs import (
    record_llm_cost,
    release_llm_cost_reservation,
    reserve_llm_cost,
)
from tcg_pipeline.news.llm import (
    DEFAULT_TRIAGE_MODEL,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_VERCEL_AI_GATEWAY,
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
from tcg_pipeline.news.prompts import RenderedPrompt, render_triage_prompt
from tcg_pipeline.settings import Settings, get_settings

TRIAGE_TRIGGERED_BY = "initial"
TRIAGE_ESTIMATED_COST_USD = Decimal("0.00625")
TRIAGE_MAX_TOKENS = 300
TRIAGE_TEMPERATURE = 0
UNCERTAINTY_MARKERS = (
    "might be",
    "possibly",
    "unclear",
    "not clear",
    "could be",
    "may be",
    "appears to",
    "seems to",
)


@dataclass(frozen=True, slots=True)
class TriageLLMResponse:
    text: str
    model: str
    usage: LLMUsage
    latency_ms: int
    stop_reason: str | None = None
    provider: str = LLM_PROVIDER_ANTHROPIC


@dataclass(frozen=True, slots=True)
class TriageDecision:
    relevant: bool
    reason: str
    original_relevant: bool
    overridden_to_relevant: bool


@dataclass(frozen=True, slots=True)
class ParsedTriageResponse:
    decision: TriageDecision | None
    parse_status: str
    parse_error_text: str | None
    output_json: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class NewsTriageRunResult:
    article_id: uuid.UUID
    extraction_id: uuid.UUID | None
    triage_status: str
    relevant: bool | None
    reason: str | None
    parse_status: str | None
    skipped_reason: str | None = None


class TriageLLMClient(Protocol):
    model: str

    def triage(self, prompt: RenderedPrompt) -> TriageLLMResponse:
        ...


class AnthropicTriageClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_TRIAGE_MODEL,
        max_tokens: int = TRIAGE_MAX_TOKENS,
    ) -> None:
        self.model = model
        self.provider = LLM_PROVIDER_ANTHROPIC
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def triage(self, prompt: RenderedPrompt) -> TriageLLMResponse:
        started_at = time.perf_counter()
        response = create_anthropic_message(
            self._client,
            model=self.model,
            max_tokens=self._max_tokens,
            temperature=TRIAGE_TEMPERATURE,
            system=[
                {
                    "type": "text",
                    "text": prompt.system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt.user_text}],
                }
            ],
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        text = "\n".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        usage = anthropic_usage(response.usage)
        return TriageLLMResponse(
            text=text,
            model=response.model,
            usage=usage,
            latency_ms=latency_ms,
            stop_reason=response.stop_reason,
            provider=self.provider,
        )


class OpenAITriageClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider: str,
        base_url: str,
        max_tokens: int = TRIAGE_MAX_TOKENS,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.model = model
        self.provider = normalize_llm_provider(provider)
        self._client = OpenAIResponsesJSONClient(
            api_key=api_key,
            model=model,
            provider=self.provider,
            base_url=base_url,
            max_output_tokens=max_tokens,
            temperature=TRIAGE_TEMPERATURE,
            timeout_seconds=timeout_seconds,
        )

    def triage(self, prompt: RenderedPrompt) -> TriageLLMResponse:
        response = self._client.create_json_response(
            system_text=prompt.system_text,
            user_text=prompt.user_text,
            schema=prompt.schema,
            schema_name="news_triage",
        )
        return TriageLLMResponse(
            text=response.text,
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
            stop_reason=response.stop_reason,
            provider=response.provider,
        )


def run_news_triage_for_article(
    article_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    client: TriageLLMClient | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
) -> NewsTriageRunResult:
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or get_session_factory()
    current = now or datetime.now(UTC)
    with resolved_session_factory() as session:
        article = session.get(NewsArticle, article_id)
        if article is None:
            raise RuntimeError("News triage references a missing article.")
        if article.fetch_status != NewsFetchStatus.FETCHED.value or not article.body_text:
            return NewsTriageRunResult(
                article_id=article_id,
                extraction_id=None,
                triage_status=article.triage_status,
                relevant=None,
                reason=None,
                parse_status=None,
                skipped_reason="article_not_fetched",
            )
        rendered_prompt = render_triage_prompt(article)

    provider = normalize_llm_provider(resolved_settings.news_triage_provider)
    if client is None and not provider_api_key(resolved_settings, provider):
        with resolved_session_factory() as session:
            _raise_missing_api_key_alert(session, provider=provider, now=current)
            session.commit()
        return NewsTriageRunResult(
            article_id=article_id,
            extraction_id=None,
            triage_status=NewsTriageStatus.PENDING.value,
            relevant=None,
            reason=None,
            parse_status=None,
            skipped_reason="no_api_key",
    )

    triage_client = client or build_triage_client(resolved_settings)
    pricing_for_model(triage_client.model)
    with resolved_session_factory() as session:
        reservation = reserve_llm_cost(
            session,
            pass_name=NewsExtractionPass.TRIAGE.value,
            model=triage_client.model,
            provider=_client_provider(triage_client),
            estimated_cost_usd=TRIAGE_ESTIMATED_COST_USD,
            now=current,
        )
        session.commit()
    if reservation is None:
        return NewsTriageRunResult(
            article_id=article_id,
            extraction_id=None,
            triage_status=NewsTriageStatus.PENDING.value,
            relevant=None,
            reason=None,
            parse_status=None,
            skipped_reason="cost_cap",
        )

    try:
        llm_response = triage_client.triage(rendered_prompt)
    except Exception as exc:
        with resolved_session_factory() as session:
            release_llm_cost_reservation(
                session,
                reserved_cost_usd=TRIAGE_ESTIMATED_COST_USD,
                now=current,
            )
            result = _persist_triage_api_error(
                session,
                article_id=article_id,
                rendered_prompt=rendered_prompt,
                model=triage_client.model,
                provider=_client_provider(triage_client),
                error=exc,
                now=current,
            )
            session.commit()
        raise RuntimeError("News triage LLM call failed.") from exc

    with resolved_session_factory() as session:
        result = persist_triage_response(
            session,
            article_id=article_id,
            rendered_prompt=rendered_prompt,
            llm_response=llm_response,
            reserved_cost_usd=TRIAGE_ESTIMATED_COST_USD,
            now=current,
        )
        session.commit()
    return result


def build_anthropic_triage_client(settings: Settings) -> AnthropicTriageClient:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for news triage.")
    return AnthropicTriageClient(
        api_key=settings.anthropic_api_key,
        model=settings.news_triage_model,
        max_tokens=settings.news_triage_max_tokens,
    )


def build_triage_client(settings: Settings) -> TriageLLMClient:
    provider = normalize_llm_provider(settings.news_triage_provider)
    if provider == LLM_PROVIDER_ANTHROPIC:
        return build_anthropic_triage_client(settings)
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        api_key = provider_api_key(settings, provider)
        if not api_key:
            raise RuntimeError(f"{provider} API key is required for news triage.")
        return OpenAITriageClient(
            api_key=api_key,
            model=settings.news_triage_model,
            provider=provider,
            base_url=provider_base_url(settings, provider),
            max_tokens=settings.news_triage_max_tokens,
            timeout_seconds=settings.news_llm_timeout_seconds,
        )
    raise RuntimeError(f"Unsupported news triage provider: {provider}")


def persist_triage_response(
    session: Session,
    *,
    article_id: uuid.UUID,
    rendered_prompt: RenderedPrompt,
    llm_response: TriageLLMResponse,
    reserved_cost_usd: Decimal = Decimal("0"),
    now: datetime | None = None,
) -> NewsTriageRunResult:
    current = now or datetime.now(UTC)
    parsed = parse_triage_response(
        llm_response.text,
        stop_reason=llm_response.stop_reason,
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
        pass_name=NewsExtractionPass.TRIAGE.value,
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
    article = session.get(NewsArticle, article_id)
    if article is None:
        raise RuntimeError("News triage references a missing article.")
    extraction = NewsExtraction(
        article_id=article_id,
        pass_name=NewsExtractionPass.TRIAGE.value,
        triggered_by=TRIAGE_TRIGGERED_BY,
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
        output_json=parsed.output_json,
        raw_response_text=llm_response.text,
        parse_status=parsed.parse_status,
        parse_error_text=parsed.parse_error_text,
        diagnostic={"stop_reason": llm_response.stop_reason},
    )
    if parsed.decision is not None:
        extraction.diagnostic = {
            "stop_reason": llm_response.stop_reason,
            "original_relevant": parsed.decision.original_relevant,
            "overridden_to_relevant": parsed.decision.overridden_to_relevant,
        }
    session.add(extraction)
    session.flush()
    article.triage_extraction_id = extraction.id
    article.triage_at = current
    if parsed.decision is None:
        article.triage_status = NewsTriageStatus.ERROR.value
    elif parsed.decision.relevant:
        article.triage_status = NewsTriageStatus.RELEVANT.value
    else:
        article.triage_status = NewsTriageStatus.NOT_RELEVANT.value
    session.flush()
    return NewsTriageRunResult(
        article_id=article_id,
        extraction_id=extraction.id,
        triage_status=article.triage_status,
        relevant=parsed.decision.relevant if parsed.decision else None,
        reason=parsed.decision.reason if parsed.decision else None,
        parse_status=parsed.parse_status,
    )


def parse_triage_response(raw_text: str, *, stop_reason: str | None = None) -> ParsedTriageResponse:
    text = _strip_json_fence(raw_text).strip()
    parse_status = NewsExtractionParseStatus.OK.value
    if stop_reason == "max_tokens":
        parse_status = NewsExtractionParseStatus.TRUNCATED.value
    elif stop_reason == "refusal":
        parse_status = NewsExtractionParseStatus.REFUSED.value
    if parse_status != NewsExtractionParseStatus.OK.value:
        return ParsedTriageResponse(
            decision=None,
            parse_status=parse_status,
            parse_error_text=stop_reason,
            output_json=None,
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedTriageResponse(
            decision=None,
            parse_status=(
                parse_status
                if parse_status != NewsExtractionParseStatus.OK.value
                else NewsExtractionParseStatus.PARSE_ERROR.value
            ),
            parse_error_text=str(exc),
            output_json=None,
        )
    if (
        not isinstance(payload, dict)
        or set(payload) - {"relevant", "reason"}
        or not isinstance(payload.get("relevant"), bool)
        or not isinstance(payload.get("reason"), str)
        or not payload["reason"].strip()
    ):
        return ParsedTriageResponse(
            decision=None,
            parse_status=NewsExtractionParseStatus.SCHEMA_INVALID.value,
            parse_error_text="Triage response did not match the required schema.",
            output_json=payload if isinstance(payload, dict) else {"value": payload},
        )
    original_relevant = payload["relevant"]
    reason = payload["reason"].strip()
    relevant = original_relevant
    overridden = False
    if not relevant and _reason_is_uncertain(reason):
        relevant = True
        overridden = True
    decision = TriageDecision(
        relevant=relevant,
        reason=reason,
        original_relevant=original_relevant,
        overridden_to_relevant=overridden,
    )
    output_json = {
        "relevant": relevant,
        "reason": reason,
        "original_relevant": original_relevant,
        "overridden_to_relevant": overridden,
    }
    return ParsedTriageResponse(
        decision=decision,
        parse_status=parse_status,
        parse_error_text=(
            None if parse_status == NewsExtractionParseStatus.OK.value else stop_reason
        ),
        output_json=output_json,
    )


def _persist_triage_api_error(
    session: Session,
    *,
    article_id: uuid.UUID,
    rendered_prompt: RenderedPrompt,
    model: str,
    provider: str,
    error: Exception,
    now: datetime,
) -> NewsTriageRunResult:
    article = session.get(NewsArticle, article_id)
    if article is None:
        raise RuntimeError("News triage references a missing article.")
    extraction = NewsExtraction(
        article_id=article_id,
        pass_name=NewsExtractionPass.TRIAGE.value,
        triggered_by=TRIAGE_TRIGGERED_BY,
        prompt_id=rendered_prompt.prompt_id,
        prompt_version=rendered_prompt.prompt_version,
        prompt_hash=rendered_prompt.prompt_hash,
        model=model,
        model_provider=provider,
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
    )
    session.add(extraction)
    session.flush()
    article.triage_status = NewsTriageStatus.ERROR.value
    article.triage_at = now
    article.triage_extraction_id = extraction.id
    session.flush()
    return NewsTriageRunResult(
        article_id=article_id,
        extraction_id=extraction.id,
        triage_status=NewsTriageStatus.ERROR.value,
        relevant=None,
        reason=None,
        parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
    )


def _raise_missing_api_key_alert(
    session: Session,
    *,
    provider: str,
    now: datetime,
) -> None:
    alert_key = _missing_api_key_alert_key(provider)
    provider_label = _provider_label(provider)
    statement = (
        insert(SystemAlert)
        .values(
            alert_key=alert_key,
            severity="warning",
            scope={"component": "news_triage"},
            message=f"{provider_label} API key is not configured; news triage is skipped.",
            detail={"skipped_reason": "no_api_key", "provider": provider},
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
                "message": f"{provider_label} API key is not configured; news triage is skipped.",
                "detail": {"skipped_reason": "no_api_key", "provider": provider},
                "last_seen_at": now,
            },
        )
    )
    session.execute(statement)


def _strip_json_fence(raw_text: str) -> str:
    text = raw_text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)
    return text


def _reason_is_uncertain(reason: str) -> bool:
    folded = reason.casefold()
    return any(marker in folded for marker in UNCERTAINTY_MARKERS)


def _client_provider(client: TriageLLMClient) -> str:
    return str(getattr(client, "provider", LLM_PROVIDER_ANTHROPIC))


def _missing_api_key_alert_key(provider: str) -> str:
    if provider == LLM_PROVIDER_ANTHROPIC:
        return "news_anthropic_api_key_missing"
    if provider == LLM_PROVIDER_OPENAI:
        return "news_openai_api_key_missing"
    if provider == LLM_PROVIDER_VERCEL_AI_GATEWAY:
        return "news_ai_gateway_api_key_missing"
    return "news_llm_api_key_missing"


def _provider_label(provider: str) -> str:
    if provider == LLM_PROVIDER_ANTHROPIC:
        return "ANTHROPIC_API_KEY"
    if provider == LLM_PROVIDER_OPENAI:
        return "OPENAI_API_KEY"
    if provider == LLM_PROVIDER_VERCEL_AI_GATEWAY:
        return "AI_GATEWAY_API_KEY"
    return provider
