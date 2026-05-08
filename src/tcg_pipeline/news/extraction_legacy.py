from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
)
from tcg_pipeline.matching.normalizer import normalize_address
from tcg_pipeline.news.costs import release_llm_cost_reservation, reserve_llm_cost
from tcg_pipeline.news.extraction import (
    ExtractionLLMClient,
    NewsExtractionRunResult,
    _client_provider,
    _persist_extraction_api_error,
    _raise_missing_api_key_alert,
    build_extraction_client,
    persist_extraction_response,
)
from tcg_pipeline.news.llm import normalize_llm_provider, pricing_for_model, provider_api_key
from tcg_pipeline.news.prompts import render_reextraction_prompt
from tcg_pipeline.settings import Settings, get_settings

PASS3A_TRIGGER_PASS1_PASS2_CONFLICT = "pass1_pass2_conflict"
PASS3A_TRIGGER_LOW_CONFIDENCE = "pass2_low_confidence"
PASS3A_TRIGGER_PARSE_ERROR = "pass2_parse_error"
PASS3B_TRIGGER_NEW_CANDIDATE = "pass2_new_candidate"
REEXTRACTION_ESTIMATED_COST_USD = Decimal("0.75")
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
    "workforce_units": "candidate_unit_workforce",
    "developer": "candidate_developer",
    "date_delivery": "candidate_delivery_year_normalized",
    "candidate_address": "candidate_address",
}
MAX_PASS3A_CONTEXT_ITEMS = 20
PASS3A_FIELD_WINDOW_PADDING = 40
PASS3A_UNIT_TOLERANCE = 5
PASS3A_UNIT_FIELDS = frozenset(
    {"total_units", "affordable_units", "market_rate_units", "workforce_units"}
)
ADDRESS_CITY_BY_SCOPE_SLUG = {
    "los_angeles": "Los Angeles",
    "city_of_los_angeles": "Los Angeles",
    "santa_monica": "Santa Monica",
    "city_of_santa_monica": "Santa Monica",
}


@dataclass(frozen=True, slots=True)
class Pass3aDecision:
    triggered_by: str
    context: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AddressConflictContext:
    city: str | None
    state: str | None
    market_slug: str | None


def run_news_reextraction_for_article(
    article_id: uuid.UUID,
    *,
    triggered_by: str,
    trigger_context: dict[str, Any],
    prior_extraction_id: uuid.UUID | None = None,
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
            raise RuntimeError("News re-extraction references a missing article.")
        effective_prior_extraction_id = prior_extraction_id or article.current_extraction_id
        if effective_prior_extraction_id is None:
            return NewsExtractionRunResult(
                article_id=article_id,
                extraction_id=None,
                relevance=None,
                reference_count=0,
                parse_status=None,
                skipped_reason="no_current_extraction",
                triggered_by=triggered_by,
            )
        prior_extraction = session.get(NewsExtraction, effective_prior_extraction_id)
        if prior_extraction is None:
            raise RuntimeError("News re-extraction references a missing extraction.")
        rendered_prompt = render_reextraction_prompt(
            session,
            article,
            prior_extraction=prior_extraction,
            trigger_context=trigger_context,
        )

    provider = normalize_llm_provider(resolved_settings.news_extract_provider)
    if client is None and not provider_api_key(resolved_settings, provider):
        with resolved_session_factory() as session:
            _raise_missing_api_key_alert(session, provider=provider, now=current)
            session.commit()
        return NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=None,
            relevance=None,
            reference_count=0,
            parse_status=None,
            skipped_reason="no_api_key",
            triggered_by=triggered_by,
        )

    extraction_client = client or build_extraction_client(resolved_settings)
    pricing_for_model(extraction_client.model)
    with resolved_session_factory() as session:
        reservation = reserve_llm_cost(
            session,
            pass_name=NewsExtractionPass.REEXTRACTION.value,
            model=extraction_client.model,
            provider=_client_provider(extraction_client),
            estimated_cost_usd=REEXTRACTION_ESTIMATED_COST_USD,
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
            triggered_by=triggered_by,
        )

    try:
        llm_response = extraction_client.extract(rendered_prompt)
    except Exception as exc:
        with resolved_session_factory() as session:
            release_llm_cost_reservation(
                session,
                reserved_cost_usd=REEXTRACTION_ESTIMATED_COST_USD,
                now=current,
            )
            error_result = _persist_extraction_api_error(
                session,
                article_id=article_id,
                rendered_prompt=rendered_prompt,
                model=extraction_client.model,
                provider=_client_provider(extraction_client),
                error=exc,
                now=current,
                pass_name=NewsExtractionPass.REEXTRACTION.value,
                triggered_by=triggered_by,
                supersedes_extraction_id=effective_prior_extraction_id,
                extra_diagnostic={"pass3b_context": trigger_context},
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
            triggered_by=triggered_by,
        )

    with resolved_session_factory() as session:
        result = persist_extraction_response(
            session,
            article_id=article_id,
            rendered_prompt=rendered_prompt,
            llm_response=llm_response,
            reserved_cost_usd=REEXTRACTION_ESTIMATED_COST_USD,
            now=current,
            pass_name=NewsExtractionPass.REEXTRACTION.value,
            triggered_by=triggered_by,
            supersedes_extraction_id=effective_prior_extraction_id,
            extra_diagnostic={"pass3b_context": trigger_context},
        )
        session.commit()
    return result


def maybe_run_pass3a_reextraction(
    *,
    article_id: uuid.UUID,
    prior_result: NewsExtractionRunResult,
    client: ExtractionLLMClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
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
        if (
            decision.triggered_by == PASS3A_TRIGGER_LOW_CONFIDENCE
            and not settings.news_use_legacy_pass3
        ):
            return None
        if (
            decision.triggered_by == PASS3A_TRIGGER_PASS1_PASS2_CONFLICT
            and settings.agent_enabled_for_news
            and settings.agent_allow_live_llm
            and not settings.news_use_legacy_pass3
        ):
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
            provider=_client_provider(client),
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
                provider=_client_provider(client),
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


def merge_pass3a_result(
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
    address_context = _address_conflict_context(article)
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
                address_context=address_context,
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
    all_windows = _reference_offset_windows(reference)
    if not all_windows:
        return signals if reference_count == 1 else []

    selected: list[dict[str, Any]] = []
    for signal in signals:
        fields = _signal_passage_fields(signal)
        windows = _reference_offset_windows(reference, fields=fields)
        if not windows:
            continue
        if _signal_overlaps_windows(signal, windows):
            selected.append(signal)
    return selected


def _signal_passage_fields(signal: dict[str, Any]) -> set[str]:
    extractor = signal.get("extractor")
    if extractor == "unit_count":
        return {"candidate_unit_total"}
    if extractor == "status_phrase":
        metadata = signal.get("metadata") if isinstance(signal.get("metadata"), dict) else {}
        if metadata.get("signal_kind") == "pipeline_status":
            return {"candidate_status_signal"}
        return set()
    if extractor == "delivery_phrase":
        return {"candidate_delivery_year_text", "candidate_delivery_year_normalized"}
    if extractor == "address":
        return {"candidate_address"}
    if extractor == "affordable_split_phrase":
        structural = signal.get("canonical")
        if not isinstance(structural, dict):
            return set()
        kind = str(structural.get("kind") or "")
        if kind in {"affordable", "low_income", "workforce", "moderate_income"}:
            if kind == "workforce":
                return {"candidate_unit_workforce"}
            return {"candidate_unit_affordable"}
        if kind == "market_rate":
            return {"candidate_unit_market_rate"}
        return set()
    if extractor == "developer_dict":
        return {"candidate_developer"}
    if extractor == "project_dict":
        return {"candidate_name", "registry_project_id"}
    return set()


def _signal_overlaps_windows(
    signal: dict[str, Any],
    windows: list[tuple[int, int]],
) -> bool:
    start = _int_or_none(signal.get("offset_start"))
    end = _int_or_none(signal.get("offset_end"))
    if start is None or end is None:
        return False
    return any(start <= window_end and end >= window_start for window_start, window_end in windows)


def _reference_offset_windows(
    reference: dict[str, Any],
    *,
    fields: set[str] | None = None,
) -> list[tuple[int, int]]:
    excerpts = reference.get("passage_excerpts") or []
    windows: list[tuple[int, int]] = []
    if not isinstance(excerpts, list):
        return windows
    for excerpt in excerpts:
        if not isinstance(excerpt, dict):
            continue
        if fields is not None and excerpt.get("field") not in fields:
            continue
        start = _int_or_none(excerpt.get("offset_start"))
        end = _int_or_none(excerpt.get("offset_end"))
        if start is None or end is None:
            continue
        windows.append(
            (
                max(start - PASS3A_FIELD_WINDOW_PADDING, 0),
                end + PASS3A_FIELD_WINDOW_PADDING,
            )
        )
    return windows


def _structural_signal_conflict(
    signal: dict[str, Any],
    *,
    reference: dict[str, Any],
    reference_index: int,
    address_context: AddressConflictContext,
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
            city=address_context.city,
            state=address_context.state,
            market=address_context.market_slug,
        )
        if _addresses_equivalent(
            signal,
            extracted_address,
            normalized.canonical_address,
            address_context=address_context,
        ):
            return None
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
        if kind == "workforce":
            return _value_conflict(
                "workforce_units",
                signal,
                reference.get("candidate_unit_workforce"),
                structural_value=count,
                reference_index=reference_index,
            )
        if kind in {"affordable", "low_income", "moderate_income"}:
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
    if extractor == "project_dict":
        registry_project_id = reference.get("registry_project_id")
        if registry_project_id is None:
            return None
        return _value_conflict(
            "registry_project_id",
            signal,
            str(registry_project_id),
            reference_index=reference_index,
        )
    return None


def _addresses_equivalent(
    signal: dict[str, Any],
    extracted_address: str,
    extracted_canonical_address: str | None,
    *,
    address_context: AddressConflictContext,
) -> bool:
    raw_match = signal.get("raw_match")
    if isinstance(raw_match, str) and _contains_normalized_text(extracted_address, raw_match):
        return True
    structural = signal.get("canonical")
    structural_value = structural.get("canonical_address") if isinstance(structural, dict) else None
    normalized_structural = None
    if isinstance(raw_match, str) and raw_match.strip():
        normalized_structural = normalize_address(
            raw_match,
            city=address_context.city,
            state=address_context.state,
            market=address_context.market_slug,
        )
    if (
        normalized_structural is not None
        and normalized_structural.canonical_address is not None
        and extracted_canonical_address is not None
        and _normalized_compare_value(normalized_structural.canonical_address)
        == _normalized_compare_value(extracted_canonical_address)
    ):
        return True
    if normalized_structural is not None:
        extracted = normalize_address(
            extracted_address,
            city=address_context.city,
            state=address_context.state,
            market=address_context.market_slug,
        )
        structural_signature = _address_signature(normalized_structural)
        extracted_signature = _address_signature(extracted)
        if (
            structural_signature is not None
            and extracted_signature is not None
            and structural_signature == extracted_signature
        ):
            return True
    return (
        isinstance(structural_value, str)
        and extracted_canonical_address is not None
        and _normalized_compare_value(structural_value)
        == _normalized_compare_value(extracted_canonical_address)
    )


def _contains_normalized_text(haystack: str, needle: str) -> bool:
    return _compact_text(needle) in _compact_text(haystack)


def _compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _address_signature(value: Any) -> tuple[Any, ...] | None:
    if value.house_number_start is None or value.street_name is None:
        return None
    return (
        value.house_number_start,
        value.house_number_end,
        value.street_predirectional,
        value.street_name,
        value.street_suffix,
        value.street_postdirectional,
    )


def _address_conflict_context(article: NewsArticle) -> AddressConflictContext:
    source = article.source
    market_slug = source.market.slug if source is not None and source.market is not None else None
    jurisdiction_slug = (
        source.jurisdiction.slug if source is not None and source.jurisdiction is not None else None
    )
    # Phase H should move city defaults into market config. Until then, keep the
    # known-city fallback explicit so Santa Monica does not silently use LA.
    city = ADDRESS_CITY_BY_SCOPE_SLUG.get(jurisdiction_slug or "")
    if city is None:
        city = ADDRESS_CITY_BY_SCOPE_SLUG.get(market_slug or "")
    state = None
    if source is not None and source.jurisdiction is not None:
        state = source.jurisdiction.state
    elif source is not None and source.market is not None:
        state = source.market.state
    return AddressConflictContext(city=city, state=state, market_slug=market_slug)


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
    if _values_equivalent(field_name, resolved_structural, resolved_extracted):
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


def _values_equivalent(field_name: str, structural_value: Any, extracted_value: Any) -> bool:
    if field_name in PASS3A_UNIT_FIELDS:
        structural_int = _int_or_none(structural_value)
        extracted_int = _int_or_none(extracted_value)
        if structural_int is not None and extracted_int is not None:
            return abs(structural_int - extracted_int) <= PASS3A_UNIT_TOLERANCE
    return _normalized_compare_value(structural_value) == _normalized_compare_value(extracted_value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _trim_pass3a_context(context: dict[str, Any]) -> dict[str, Any]:
    trimmed = dict(context)
    trimmed["conflicts"] = list(trimmed.get("conflicts") or [])[:MAX_PASS3A_CONTEXT_ITEMS]
    trimmed["low_confidence"] = list(trimmed.get("low_confidence") or [])[:MAX_PASS3A_CONTEXT_ITEMS]
    return trimmed
