from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory, redact_database_url
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsMatchStatus,
    NewsProjectReference,
    NewsSignalFlag,
    NewsSource,
    NewsTriageStatus,
    ScrapeJobKind,
)
from tcg_pipeline.matching.news_matcher import match_news_reference
from tcg_pipeline.news.extraction import (
    AnthropicExtractionClient,
    ExtractionLLMClient,
    ExtractionLLMResponse,
    NewsExtractionRunResult,
    OpenAIExtractionClient,
    _reference_from_payload,
    build_extraction_client,
    decide_pass3a_reextraction,
    parse_extraction_response,
)
from tcg_pipeline.news.integration import NewsIntegrationResult, run_news_integration_for_article
from tcg_pipeline.news.llm import (
    calculate_llm_cost_usd,
    create_anthropic_message,
    normalize_llm_provider,
    pricing_assumption_for_model,
    pricing_for_model,
    provider_api_key,
)
from tcg_pipeline.news.prompts import RenderedPrompt, render_extraction_prompt
from tcg_pipeline.news.structural import build_structural_signals_payload
from tcg_pipeline.settings import Settings, get_settings

DEFAULT_AB_CANDIDATES = "anthropic:claude-opus-4-7,anthropic:claude-sonnet-4-6,openai:gpt-5.4"
DEFAULT_AB_FIXTURE = Path("tests/fixtures/news/urbanize_la/pass1_validation_articles.json")
PREFLIGHT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
HARNESS_COST_ACCOUNTING_NOTE = (
    "The A/B harness calls provider clients directly and does not write "
    "reserve_llm_cost/record_llm_cost rows. Use this report's measured usage "
    "and cost fields as the audit trail for harness spend."
)
HARNESS_CACHE_COMPARISON_NOTE = (
    "Provider cache semantics are not fully apples-to-apples. Anthropic prompt "
    "cache may reduce articles after the first candidate call; OpenAI-compatible "
    "providers report cached tokens differently. The slim extract_v2 prompt keeps "
    "the absolute effect small, but close model-cost results should be interpreted "
    "with this caveat."
)


@dataclass(frozen=True, slots=True)
class ABExtractionCandidate:
    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class ABArticleFixture:
    slug: str
    url: str
    title: str
    body_text: str
    published_at: datetime | None = None


def parse_candidate_specs(candidates: str) -> tuple[ABExtractionCandidate, ...]:
    parsed: list[ABExtractionCandidate] = []
    for raw_spec in candidates.split(","):
        spec = raw_spec.strip()
        if not spec:
            continue
        if ":" not in spec:
            raise ValueError(
                f"Invalid candidate '{spec}'. Use '<provider>:<model>', "
                "for example 'anthropic:claude-sonnet-4-6'."
            )
        provider, model = spec.split(":", maxsplit=1)
        provider = normalize_llm_provider(provider)
        model = model.strip()
        if not model:
            raise ValueError(f"Invalid candidate '{spec}': model is empty.")
        pricing_for_model(model)
        parsed.append(ABExtractionCandidate(provider=provider, model=model))
    if not parsed:
        raise ValueError("At least one extraction candidate is required.")
    return tuple(parsed)


def load_article_fixtures(fixture_path: Path) -> tuple[ABArticleFixture, ...]:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("A/B fixture must be a JSON list of article objects.")
    fixtures: list[ABArticleFixture] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"A/B fixture row {index} must be an object.")
        slug = _required_fixture_string(item, "slug", index)
        url = _required_fixture_string(item, "url", index)
        title = _required_fixture_string(item, "title", index)
        body_text = _required_fixture_string(item, "body_text", index)
        fixtures.append(
            ABArticleFixture(
                slug=slug,
                url=url,
                title=title,
                body_text=body_text,
                published_at=_fixture_datetime(item.get("published_at")),
            )
        )
    return tuple(fixtures)


def run_extraction_ab_harness(
    *,
    fixture_path: Path = DEFAULT_AB_FIXTURE,
    candidates: str = DEFAULT_AB_CANDIDATES,
    source_slug: str = "urbanize_la",
    output_path: Path | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
    run_preflight: bool = True,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or get_session_factory()
    started_at = now or datetime.now(UTC)
    candidate_specs = parse_candidate_specs(candidates)
    _validate_candidate_credentials(resolved_settings, candidate_specs)
    fixtures = load_article_fixtures(fixture_path)
    if limit is not None:
        fixtures = fixtures[:limit]
    if not fixtures:
        raise ValueError("A/B fixture did not contain any articles to run.")

    clients = {
        candidate.key: _build_candidate_client(resolved_settings, candidate)
        for candidate in candidate_specs
    }
    preflight_results = (
        preflight_candidate_clients(clients, candidate_specs)
        if run_preflight
        else _skipped_preflight_results(candidate_specs)
    )

    candidate_results: dict[str, list[dict[str, Any]]] = {
        candidate.key: [] for candidate in candidate_specs
    }
    run_id = uuid.uuid4()
    with resolved_session_factory() as session:
        source = _load_news_source(session, source_slug)
        for fixture in fixtures:
            article = _transient_article(
                fixture,
                source=source,
                run_id=run_id,
                source_slug=source_slug,
            )
            market_slug = source.market.slug if source.market is not None else None
            article.structural_signals = build_structural_signals_payload(
                article.body_text or "",
                title_text=article.title,
                session=session,
                market_slug=market_slug,
                market_id=source.market_id,
                published_at=article.published_at,
                now=started_at,
            )
            article.structural_signals_at = started_at
            prompt = render_extraction_prompt(session, article)
            active_signal_flags = _active_signal_flag_keys(session)
            for candidate in candidate_specs:
                result = _run_candidate_for_article(
                    session,
                    session_factory=resolved_session_factory,
                    client=clients[candidate.key],
                    candidate=candidate,
                    fixture=fixture,
                    source_slug=source_slug,
                    prompt=prompt,
                    article=article,
                    active_signal_flags=active_signal_flags,
                    now=started_at,
                    run_id=run_id,
                )
                candidate_results[candidate.key].append(result)

    report = {
        "generated_at": started_at.isoformat(),
        "fixture_path": str(fixture_path),
        "source_slug": source_slug,
        "database_url_redacted": _redacted_database_url(resolved_settings),
        "prompt_id": _first_prompt_id(candidate_results),
        "preflight_results": preflight_results,
        "cost_accounting": {
            "llm_cost_usage_written": False,
            "note": HARNESS_COST_ACCOUNTING_NOTE,
            "cache_comparison_note": HARNESS_CACHE_COMPARISON_NOTE,
        },
        "candidate_summaries": summarize_candidate_results(candidate_results),
        "article_results": candidate_results,
        "grading_contract": {
            "score_scale": "1-5",
            "dimensions": [
                "factual_correctness",
                "completeness",
                "field_attribution_fidelity",
            ],
            "fields_to_fill": [
                "payload_quality_spot_grade.score",
                "payload_quality_spot_grade.notes",
            ],
        },
    }
    destination = output_path or _default_output_path(resolved_settings, started_at)
    report["output_path"] = str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def preflight_candidate_clients(
    clients: dict[str, ExtractionLLMClient],
    candidates: tuple[ABExtractionCandidate, ...],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        client = clients[candidate.key]
        started_at = time.perf_counter()
        try:
            _preflight_candidate_client(client)
        except Exception as exc:  # noqa: BLE001 - surface provider-specific failures clearly.
            raise RuntimeError(f"A/B preflight failed for {candidate.key}: {exc}") from exc
        results.append(
            {
                "candidate": candidate.key,
                "provider": candidate.provider,
                "model": candidate.model,
                "status": "ok",
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
            }
        )
    return results


def summarize_candidate_results(
    candidate_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for candidate_key, results in candidate_results.items():
        parse_counts = Counter(str(result.get("parse_status") or "error") for result in results)
        matcher_status_counts: Counter[str] = Counter()
        match_type_counts: Counter[str] = Counter()
        review_item_counts: Counter[str] = Counter()
        agent_trigger_articles = 0
        total_references = 0
        total_cost = Decimal("0")
        total_latency_ms = 0
        error_count = 0
        for result in results:
            total_references += int(result.get("reference_count") or 0)
            total_cost += Decimal(str(result.get("cost_usd") or "0"))
            total_latency_ms += int(result.get("latency_ms") or 0)
            if result.get("error_text"):
                error_count += 1
            if result.get("agent_trigger", {}).get("would_trigger"):
                agent_trigger_articles += 1
            for status, count in (result.get("matcher_status_counts") or {}).items():
                matcher_status_counts[str(status)] += int(count)
            for match_type, count in (result.get("match_type_counts") or {}).items():
                match_type_counts[str(match_type)] += int(count)
            for item_type, count in (result.get("review_item_counts") or {}).items():
                review_item_counts[str(item_type)] += int(count)
        article_count = len(results)
        summaries.append(
            {
                "candidate": candidate_key,
                "articles": article_count,
                "parse_status_counts": dict(sorted(parse_counts.items())),
                "references": total_references,
                "matcher_status_counts": dict(sorted(matcher_status_counts.items())),
                "match_type_counts": dict(sorted(match_type_counts.items())),
                "agent_trigger_articles": agent_trigger_articles,
                "agent_trigger_rate": _ratio(agent_trigger_articles, article_count),
                "review_item_counts": dict(sorted(review_item_counts.items())),
                "total_cost_usd": _decimal_string(total_cost),
                "avg_cost_usd": _decimal_string(total_cost / article_count)
                if article_count
                else "0.000000",
                "avg_latency_ms": round(total_latency_ms / article_count, 1)
                if article_count
                else 0,
                "errors": error_count,
            }
        )
    return summaries


def _run_candidate_for_article(
    session: Session,
    *,
    session_factory: sessionmaker[Session],
    client: ExtractionLLMClient,
    candidate: ABExtractionCandidate,
    fixture: ABArticleFixture,
    source_slug: str,
    prompt: RenderedPrompt,
    article: NewsArticle,
    active_signal_flags: set[str],
    now: datetime,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    try:
        llm_response = client.extract(prompt)
    except Exception as exc:  # noqa: BLE001 - harness records provider errors per candidate.
        return _error_article_result(
            candidate=candidate,
            fixture=fixture,
            prompt=prompt,
            error=exc,
        )

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
    result: dict[str, Any] = {
        "article_slug": fixture.slug,
        "url": fixture.url,
        "title": fixture.title,
        "candidate": candidate.key,
        "provider": llm_response.provider,
        "configured_model": candidate.model,
        "response_model": llm_response.model,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.prompt_version,
        "prompt_hash": prompt.prompt_hash,
        "parse_status": parsed.parse_status,
        "parse_error_text": parsed.parse_error_text,
        "unknown_signal_flags": list(parsed.unknown_signal_flags),
        "diagnostic": {},
        "reference_count": 0,
        "matcher_status_counts": {},
        "match_type_counts": {},
        "reference_results": [],
        "agent_trigger": {
            "would_trigger": False,
            "reasons": [],
        },
        "review_item_counts": {
            "possible_match": 0,
            "new_candidate": 0,
            "status_change": 0,
            "total_projected": 0,
        },
        "review_projection_error": None,
        "cost_usd": _decimal_string(cost_usd),
        "pricing_assumption": pricing_assumption_for_model(llm_response.model),
        "usage": {
            "input_tokens_uncached": llm_response.usage.input_tokens_uncached,
            "input_tokens_cache_creation": llm_response.usage.input_tokens_cache_creation,
            "input_tokens_cached": llm_response.usage.input_tokens_cached,
            "output_tokens": llm_response.usage.output_tokens,
        },
        "latency_ms": llm_response.latency_ms,
        "stop_reason": llm_response.stop_reason,
        "payload_quality_spot_grade": {
            "score": None,
            "notes": None,
        },
    }
    if parsed.payload is None or parsed.parse_status != NewsExtractionParseStatus.OK.value:
        return result
    diagnostic = parsed.payload.get("diagnostic")
    if isinstance(diagnostic, dict):
        result["diagnostic"] = diagnostic

    extraction = _transient_extraction(
        article=article,
        prompt=prompt,
        llm_response=llm_response,
        payload=parsed.payload,
        cost_usd=cost_usd,
    )
    references = _transient_references(article=article, extraction=extraction)
    matcher_status_counts: Counter[str] = Counter()
    match_type_counts: Counter[str] = Counter()
    reference_results: list[dict[str, Any]] = []
    for reference in references:
        match = match_news_reference(session, article=article, reference=reference)
        matcher_status_counts[match.status.value] += 1
        match_type_counts[match.match_type] += 1
        reference_results.append(_reference_result(reference, match))

    agent_reasons = _agent_trigger_reasons(article, extraction, matcher_status_counts)
    result.update(
        {
            "reference_count": len(references),
            "matcher_status_counts": dict(sorted(matcher_status_counts.items())),
            "match_type_counts": dict(sorted(match_type_counts.items())),
            "reference_results": reference_results,
            "agent_trigger": {
                "would_trigger": bool(agent_reasons),
                "reasons": agent_reasons,
            },
        }
    )
    projection = _project_review_counts_with_rollback(
        session_factory,
        fixture=fixture,
        source_slug=source_slug,
        prompt=prompt,
        llm_response=llm_response,
        parsed_payload=parsed.payload,
        structural_signals=article.structural_signals or {},
        now=now,
        run_id=run_id,
    )
    result["review_item_counts"] = projection["review_item_counts"]
    result["review_projection_error"] = projection["error"]
    return result


def _project_review_counts_with_rollback(
    session_factory: sessionmaker[Session],
    *,
    fixture: ABArticleFixture,
    source_slug: str,
    prompt: RenderedPrompt,
    llm_response: ExtractionLLMResponse,
    parsed_payload: dict[str, Any],
    structural_signals: dict[str, Any],
    now: datetime,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    try:
        engine = _session_factory_engine(session_factory)
        with engine.connect() as connection:
            transaction = connection.begin()
            rollback_factory = sessionmaker(
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
                class_=Session,
            )
            try:
                article_id = _persist_harness_extraction(
                    rollback_factory,
                    fixture=fixture,
                    source_slug=source_slug,
                    prompt=prompt,
                    llm_response=llm_response,
                    parsed_payload=parsed_payload,
                    structural_signals=structural_signals,
                    now=now,
                    run_id=run_id,
                )
                integration = run_news_integration_for_article(
                    article_id,
                    session_factory=rollback_factory,
                    reextraction_runner=_disabled_reextraction_runner,
                    now=now,
                )
            finally:
                transaction.rollback()
        return {
            "review_item_counts": _review_counts_from_integration(integration),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - projection failure should not discard LLM output.
        return {
            "review_item_counts": {
                "possible_match": 0,
                "new_candidate": 0,
                "status_change": 0,
                "total_projected": 0,
            },
            "error": str(exc),
        }


def _persist_harness_extraction(
    session_factory: sessionmaker[Session],
    *,
    fixture: ABArticleFixture,
    source_slug: str,
    prompt: RenderedPrompt,
    llm_response: ExtractionLLMResponse,
    parsed_payload: dict[str, Any],
    structural_signals: dict[str, Any],
    now: datetime,
    run_id: uuid.UUID,
) -> uuid.UUID:
    with session_factory() as session:
        source = _load_news_source(session, source_slug)
        article = _transient_article(
            fixture,
            source=source,
            run_id=run_id,
            source_slug=source_slug,
        )
        article.structural_signals = structural_signals
        article.structural_signals_at = now
        session.add(article)
        session.flush()
        extraction = NewsExtraction(
            article_id=article.id,
            pass_name=NewsExtractionPass.EXTRACTION.value,
            triggered_by="ab_harness",
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt.prompt_hash,
            model=llm_response.model,
            model_provider=llm_response.provider,
            input_tokens_uncached=llm_response.usage.input_tokens_uncached,
            input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
            input_tokens_cached=llm_response.usage.input_tokens_cached,
            output_tokens=llm_response.usage.output_tokens,
            cost_usd=calculate_llm_cost_usd(
                llm_response.model,
                input_tokens_uncached=llm_response.usage.input_tokens_uncached,
                input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
                input_tokens_cached=llm_response.usage.input_tokens_cached,
                output_tokens=llm_response.usage.output_tokens,
            ),
            latency_ms=llm_response.latency_ms,
            output_json=parsed_payload,
            raw_response_text=llm_response.text,
            parse_status=NewsExtractionParseStatus.OK.value,
            diagnostic={
                "ab_harness": True,
                "stop_reason": llm_response.stop_reason,
            },
        )
        session.add(extraction)
        session.flush()
        article.current_extraction_id = extraction.id
        article.current_extraction_version = 1
        for index, reference_payload in enumerate(parsed_payload.get("project_references") or []):
            if not isinstance(reference_payload, dict):
                continue
            session.add(
                _reference_from_payload(
                    article_id=article.id,
                    extraction_id=extraction.id,
                    reference_index=index,
                    payload=reference_payload,
                )
            )
        session.flush()
        article_id = article.id
        session.commit()
        return article_id


def _review_counts_from_integration(integration: NewsIntegrationResult) -> dict[str, int]:
    possible = integration.possible
    new_candidate = integration.new_candidate
    status_change = integration.status_change_review_items
    return {
        "possible_match": possible,
        "new_candidate": new_candidate,
        "status_change": status_change,
        "total_projected": possible + new_candidate + status_change,
    }


def _reference_result(reference: NewsProjectReference, match: Any) -> dict[str, Any]:
    delivery_date = reference.candidate_delivery_year_normalized
    return {
        "reference_index": reference.reference_index,
        "candidate_name": reference.candidate_name,
        "candidate_address": reference.candidate_address,
        "candidate_developer": reference.candidate_developer,
        "candidate_unit_total": reference.candidate_unit_total,
        "candidate_unit_affordable": reference.candidate_unit_affordable,
        "candidate_unit_market_rate": reference.candidate_unit_market_rate,
        "candidate_unit_workforce": reference.candidate_unit_workforce,
        "candidate_product_type": reference.candidate_product_type,
        "candidate_age_restriction": reference.candidate_age_restriction,
        "candidate_status_signal": reference.candidate_status_signal,
        "candidate_delivery_year_text": reference.candidate_delivery_year_text,
        "candidate_delivery_year_normalized": (
            delivery_date.isoformat() if delivery_date is not None else None
        ),
        "candidate_signal_flags": reference.candidate_signal_flags or {},
        "candidate_identifiers": reference.candidate_identifiers
        or {
            "case_number": [],
            "permit_number": [],
            "apn": [],
        },
        "candidate_neighborhood": reference.candidate_neighborhood,
        "candidate_lat": reference.candidate_lat,
        "candidate_lng": reference.candidate_lng,
        "candidate_confidence": reference.candidate_confidence,
        "passage_excerpts": reference.passage_excerpts or [],
        "match_status": match.status.value,
        "match_type": match.match_type,
        "match_confidence": match.confidence,
        "matched_project_id": str(match.project_id) if match.project_id else None,
        "candidate_project_ids": [str(project_id) for project_id in match.candidate_project_ids],
        "match_reason": match.reason,
    }


def _agent_trigger_reasons(
    article: NewsArticle,
    extraction: NewsExtraction,
    matcher_status_counts: Counter[str],
) -> list[str]:
    reasons: list[str] = []
    decision = decide_pass3a_reextraction(article, extraction)
    if decision is not None and decision.triggered_by in {
        "pass1_pass2_conflict",
        "pass2_low_confidence",
    }:
        reasons.append(decision.triggered_by)
    if matcher_status_counts.get(NewsMatchStatus.NEW_CANDIDATE.value, 0):
        reasons.append("pass2_new_candidate")
    return reasons


def _transient_article(
    fixture: ABArticleFixture,
    *,
    source: NewsSource,
    run_id: uuid.UUID,
    source_slug: str,
) -> NewsArticle:
    article_id = uuid.uuid4()
    synthetic_key = f"ab-harness:{run_id}:{source_slug}:{fixture.slug}:{article_id}"
    return NewsArticle(
        id=article_id,
        news_source_id=source.id,
        source=source,
        url_canonical=fixture.url,
        url_original=fixture.url,
        url_hash=hashlib.sha256(synthetic_key.encode("utf-8")).hexdigest(),
        fetch_status=NewsFetchStatus.FETCHED.value,
        fetched_at=datetime.now(UTC),
        body_text=fixture.body_text,
        body_text_hash=hashlib.sha256(fixture.body_text.encode("utf-8")).hexdigest(),
        title=fixture.title,
        published_at=fixture.published_at,
        triage_status=NewsTriageStatus.RELEVANT.value,
        triage_at=datetime.now(UTC),
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
        notes=f"AGENT.1 A/B harness fixture: {fixture.slug}",
    )


def _transient_extraction(
    *,
    article: NewsArticle,
    prompt: RenderedPrompt,
    llm_response: ExtractionLLMResponse,
    payload: dict[str, Any],
    cost_usd: Decimal,
) -> NewsExtraction:
    extraction = NewsExtraction(
        id=uuid.uuid4(),
        article_id=article.id,
        article=article,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="ab_harness",
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt.prompt_hash,
        model=llm_response.model,
        model_provider=llm_response.provider,
        input_tokens_uncached=llm_response.usage.input_tokens_uncached,
        input_tokens_cache_creation=llm_response.usage.input_tokens_cache_creation,
        input_tokens_cached=llm_response.usage.input_tokens_cached,
        output_tokens=llm_response.usage.output_tokens,
        cost_usd=cost_usd,
        latency_ms=llm_response.latency_ms,
        output_json=payload,
        raw_response_text=llm_response.text,
        parse_status=NewsExtractionParseStatus.OK.value,
        diagnostic={"ab_harness": True, "stop_reason": llm_response.stop_reason},
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    return extraction


def _transient_references(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
) -> list[NewsProjectReference]:
    references: list[NewsProjectReference] = []
    payload_references = extraction.output_json.get("project_references")
    if not isinstance(payload_references, list):
        return references
    for index, reference_payload in enumerate(payload_references):
        if not isinstance(reference_payload, dict):
            continue
        reference = _reference_from_payload(
            article_id=article.id,
            extraction_id=extraction.id,
            reference_index=index,
            payload=reference_payload,
        )
        reference.id = uuid.uuid4()
        reference.article = article
        reference.extraction = extraction
        references.append(reference)
    return references


def _disabled_reextraction_runner(*args: Any, **kwargs: Any) -> NewsExtractionRunResult:
    article_id = args[0] if args else kwargs.get("article_id")
    if not isinstance(article_id, uuid.UUID):
        article_id = uuid.UUID(str(article_id))
    return NewsExtractionRunResult(
        article_id=article_id,
        extraction_id=None,
        relevance=None,
        reference_count=0,
        parse_status=None,
        skipped_reason="ab_harness_reextraction_disabled",
        triggered_by=kwargs.get("triggered_by"),
    )


def _build_candidate_client(settings: Settings, candidate: ABExtractionCandidate):
    candidate_settings = settings.model_copy(
        update={
            "news_extract_provider": candidate.provider,
            "news_extract_model": candidate.model,
        }
    )
    return build_extraction_client(candidate_settings)


def _preflight_candidate_client(client: ExtractionLLMClient) -> None:
    if isinstance(client, AnthropicExtractionClient):
        create_anthropic_message(
            client._client,
            model=client.model,
            max_tokens=8,
            temperature=0,
            system="Reply with the single word ok.",
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "ping"}],
                }
            ],
        )
        return
    if isinstance(client, OpenAIExtractionClient):
        client._client.create_json_response(
            system_text="Return JSON that matches the schema.",
            user_text='Return {"ok": true}.',
            schema=PREFLIGHT_SCHEMA,
            schema_name="ab_harness_preflight",
        )
        return
    custom_preflight = getattr(client, "preflight", None)
    if callable(custom_preflight):
        custom_preflight()
        return
    raise RuntimeError(f"Unsupported A/B harness preflight client: {type(client).__name__}")


def _skipped_preflight_results(
    candidates: tuple[ABExtractionCandidate, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "candidate": candidate.key,
            "provider": candidate.provider,
            "model": candidate.model,
            "status": "skipped",
            "latency_ms": 0,
        }
        for candidate in candidates
    ]


def _validate_candidate_credentials(
    settings: Settings,
    candidates: tuple[ABExtractionCandidate, ...],
) -> None:
    missing: list[str] = []
    for candidate in candidates:
        if not provider_api_key(settings, candidate.provider):
            missing.append(candidate.provider)
    if missing:
        deduped = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Missing API key for A/B candidate provider(s): {deduped}.")


def _redacted_database_url(settings: Settings) -> str | None:
    return redact_database_url(settings.database_url) if settings.database_url else None


def _load_news_source(session: Session, source_slug: str) -> NewsSource:
    source = session.execute(
        select(NewsSource).where(NewsSource.slug == source_slug)
    ).scalar_one_or_none()
    if source is None:
        raise RuntimeError(f"News source '{source_slug}' does not exist.")
    return source


def _active_signal_flag_keys(session: Session) -> set[str]:
    return set(
        session.execute(
            select(NewsSignalFlag.flag_key).where(
                NewsSignalFlag.active.is_(True),
                NewsSignalFlag.retired_at.is_(None),
            )
        ).scalars()
    )


def _session_factory_engine(session_factory: sessionmaker[Session]) -> Engine:
    bind = session_factory.kw.get("bind")
    if isinstance(bind, Engine):
        return bind
    with session_factory() as session:
        engine = session.get_bind()
        if not isinstance(engine, Engine):
            raise RuntimeError("A/B harness requires a session factory bound to an Engine.")
        return engine


def _error_article_result(
    *,
    candidate: ABExtractionCandidate,
    fixture: ABArticleFixture,
    prompt: RenderedPrompt,
    error: Exception,
) -> dict[str, Any]:
    return {
        "article_slug": fixture.slug,
        "url": fixture.url,
        "title": fixture.title,
        "candidate": candidate.key,
        "provider": candidate.provider,
        "configured_model": candidate.model,
        "response_model": None,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.prompt_version,
        "prompt_hash": prompt.prompt_hash,
        # Harness-only synthetic status. It is intentionally not a
        # NewsExtractionParseStatus value because no provider response was parsed.
        "parse_status": "api_error",
        "parse_error_text": None,
        "unknown_signal_flags": [],
        "diagnostic": {},
        "reference_count": 0,
        "matcher_status_counts": {},
        "match_type_counts": {},
        "reference_results": [],
        "agent_trigger": {
            "would_trigger": False,
            "reasons": [],
        },
        "review_item_counts": {
            "possible_match": 0,
            "new_candidate": 0,
            "status_change": 0,
            "total_projected": 0,
        },
        "review_projection_error": None,
        "cost_usd": "0.000000",
        "pricing_assumption": pricing_assumption_for_model(candidate.model),
        "usage": {
            "input_tokens_uncached": 0,
            "input_tokens_cache_creation": 0,
            "input_tokens_cached": 0,
            "output_tokens": 0,
        },
        "latency_ms": 0,
        "stop_reason": None,
        "error_text": str(error),
        "payload_quality_spot_grade": {
            "score": None,
            "notes": None,
        },
    }


def _required_fixture_string(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"A/B fixture row {index} is missing non-empty '{key}'.")
    return value.strip()


def _fixture_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("published_at must be an ISO datetime string when provided.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _default_output_path(settings: Settings, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%d_%H%M%S")
    return settings.output_dir / "news" / f"ab_extract_{stamp}.json"


def _first_prompt_id(candidate_results: dict[str, list[dict[str, Any]]]) -> str | None:
    for results in candidate_results.values():
        for result in results:
            prompt_id = result.get("prompt_id")
            if isinstance(prompt_id, str):
                return prompt_id
    return None


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _decimal_string(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001")))
