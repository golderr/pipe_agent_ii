from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.client import build_anthropic_agent_client
from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE, AgentTrigger
from tcg_pipeline.agents.runner import (
    AgentClient,
    AgentRunResult,
    IntakeRecord,
    run_agent_for_intake,
)
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.evidence import serialize_json, write_evidence
from tcg_pipeline.db.models import (
    AgentRunOutcome,
    AgentRunReviewItem,
    AgeRestriction,
    Evidence,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsMatchStatus,
    NewsProjectReference,
    Priority,
    ProductType,
    Project,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    ScrapeTriggerType,
    SourceRun,
)
from tcg_pipeline.matching.differ import (
    DetectedChange,
    DiffResult,
    ReviewFlag,
    diff_project_snapshots,
    snapshot_project_for_diff,
)
from tcg_pipeline.matching.news_matcher import (
    NewsMatchResult,
    canonical_address_for_reference,
    match_news_reference,
)
from tcg_pipeline.matching.normalizer import normalize_address
from tcg_pipeline.news.extraction import (
    PASS3B_TRIGGER_NEW_CANDIDATE,
    ExtractionLLMClient,
    NewsExtractionRunResult,
    run_news_reextraction_for_article,
)
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.review.decision_cards import (
    proposed_value_for_payload,
    upsert_decision_card_review_item,
)
from tcg_pipeline.settings import Settings, get_settings

NEWS_SOURCE_TYPE = "news_article"
AGENT_PROMOTED_EXISTING_PROJECT_MATCH = "agent_promoted_existing_project"
AGENT_CONFIRMED_POSSIBLE_MATCH = "agent_confirmed_possible_match"
AGENT_PROMOTE_EXISTING_PROJECT_DECISION = "promote_existing_project"
AGENT_CONFIRM_EXISTING_PROJECT_DECISION = "confirm_existing_project"
ACTIVE_REVIEW_STATES = {"open", "staged"}
REFERENCE_FIELD_TO_PROJECT_FIELD = {
    "candidate_name": "project_name",
    "candidate_address": "canonical_address",
    "candidate_developer": "developer",
    "candidate_unit_total": "total_units",
    "candidate_unit_affordable": "affordable_units",
    "candidate_unit_market_rate": "market_rate_units",
    "candidate_unit_workforce": "workforce_units",
    "candidate_product_type": "product_type",
    "candidate_age_restriction": "age_restriction",
    "candidate_status_signal": "pipeline_status",
    "candidate_delivery_year_normalized": "date_delivery",
}
FIELD_TO_REFERENCE_FIELD = {
    project_field: reference_field
    for reference_field, project_field in REFERENCE_FIELD_TO_PROJECT_FIELD.items()
}
LOW_CONFIDENCE_REFERENCE_FIELDS = {
    "pipeline_status": "candidate_status_signal",
    "total_units": "candidate_unit_total",
    "affordable_units": "candidate_unit_affordable",
    "market_rate_units": "candidate_unit_market_rate",
    "workforce_units": "candidate_unit_workforce",
    "developer": "candidate_developer",
    "date_delivery": "candidate_delivery_year_normalized",
    "candidate_address": "candidate_address",
}


@dataclass(frozen=True, slots=True)
class NewsIntegrationResult:
    article_id: uuid.UUID
    source_run_id: uuid.UUID | None
    extraction_id: uuid.UUID | None
    references_processed: int = 0
    confirmed: int = 0
    possible: int = 0
    new_candidate: int = 0
    discarded: int = 0
    evidence_inserted: int = 0
    evidence_reused: int = 0
    review_items_created: int = 0
    review_items_updated: int = 0
    status_change_review_items: int = 0
    pass3b_triggered: bool = False
    pass3b_result: NewsExtractionRunResult | None = None
    skipped_reason: str | None = None
    force_project_id_dropped_reason: str | None = None

    @property
    def progress_payload(self) -> dict[str, Any]:
        return {
            "integration_skipped_reason": self.skipped_reason,
            "integration_extraction_id": str(self.extraction_id) if self.extraction_id else None,
            "integration_references_processed": self.references_processed,
            "integration_confirmed": self.confirmed,
            "integration_possible": self.possible,
            "integration_new_candidate": self.new_candidate,
            "integration_discarded": self.discarded,
            "integration_evidence_inserted": self.evidence_inserted,
            "integration_review_items_created": self.review_items_created,
            "integration_review_items_updated": self.review_items_updated,
            "integration_status_change_review_items": self.status_change_review_items,
            "pass3b_triggered": self.pass3b_triggered,
            "pass3b_extraction_id": (
                str(self.pass3b_result.extraction_id)
                if self.pass3b_result and self.pass3b_result.extraction_id
                else None
            ),
            "pass3b_parse_status": (
                self.pass3b_result.parse_status if self.pass3b_result else None
            ),
            "pass3b_skipped_reason": (
                self.pass3b_result.skipped_reason if self.pass3b_result else None
            ),
            "pass3b_error_text": self.pass3b_result.error_text if self.pass3b_result else None,
            "force_project_id_dropped_reason": self.force_project_id_dropped_reason,
        }


@dataclass(slots=True)
class _MutableIntegrationStats:
    references_processed: int = 0
    confirmed: int = 0
    possible: int = 0
    new_candidate: int = 0
    discarded: int = 0
    evidence_inserted: int = 0
    evidence_reused: int = 0
    review_items_created: int = 0
    review_items_updated: int = 0
    status_change_review_items: int = 0


@dataclass(slots=True)
class _ConfirmedReference:
    reference: NewsProjectReference
    match: NewsMatchResult
    evidence: Evidence
    agent_run_id: uuid.UUID | None = None


@dataclass(slots=True)
class _ProjectIntegrationContext:
    references: list[_ConfirmedReference] = field(default_factory=list)


ReextractionRunner = Callable[..., NewsExtractionRunResult]


def run_news_integration_for_article(
    article_id: uuid.UUID,
    *,
    source_run_id: uuid.UUID | None = None,
    force_project_id: uuid.UUID | None = None,
    session_factory: sessionmaker[Session] | None = None,
    reextraction_runner: ReextractionRunner | None = None,
    reextraction_client: ExtractionLLMClient | None = None,
    agent_client: AgentClient | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> NewsIntegrationResult:
    resolved_session_factory = session_factory or get_session_factory()
    resolved_settings = settings or get_settings()
    current = now or datetime.now(UTC)
    first_pass = _load_current_references_and_matches(
        resolved_session_factory,
        article_id=article_id,
        force_project_id=force_project_id,
    )
    if first_pass.skipped_reason is not None:
        return NewsIntegrationResult(
            article_id=article_id,
            source_run_id=source_run_id,
            extraction_id=first_pass.extraction_id,
            skipped_reason=first_pass.skipped_reason,
            force_project_id_dropped_reason=first_pass.force_project_id_dropped_reason,
        )

    pass3b_result: NewsExtractionRunResult | None = None
    pass3b_triggered = resolved_settings.news_use_legacy_pass3 and _should_run_pass3b(first_pass)
    agent_decisions: dict[uuid.UUID, _NewsAgentDecision] = {}
    if pass3b_triggered:
        runner = reextraction_runner or run_news_reextraction_for_article
        pass3b_result = runner(
            article_id,
            triggered_by=PASS3B_TRIGGER_NEW_CANDIDATE,
            trigger_context=_pass3b_context(first_pass),
            prior_extraction_id=first_pass.extraction_id,
            session_factory=resolved_session_factory,
            client=reextraction_client,
            now=current,
        )
    else:
        agent_decisions = _run_news_agents_for_first_pass(
            resolved_session_factory,
            first_pass=first_pass,
            source_run_id=source_run_id,
            force_project_id=force_project_id,
            settings=resolved_settings,
            agent_client=agent_client,
            now=current,
        )

    with resolved_session_factory() as session:
        result = _integrate_current_extraction(
            session,
            article_id=article_id,
            source_run_id=source_run_id,
            force_project_id=force_project_id,
            pass3b_triggered=pass3b_triggered,
            pass3b_result=pass3b_result,
            prior_extraction_id=first_pass.extraction_id if pass3b_triggered else None,
            agent_decisions=agent_decisions,
            now=current,
        )
        session.commit()
        return result


@dataclass(frozen=True, slots=True)
class _FirstPassMatchSet:
    article_id: uuid.UUID
    extraction_id: uuid.UUID | None
    references: tuple[NewsProjectReference, ...] = ()
    matches: tuple[NewsMatchResult, ...] = ()
    skipped_reason: str | None = None
    current_extraction_triggered_by: str | None = None
    force_project_id_dropped_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _NewsAgentDecision:
    result: AgentRunResult


def _load_current_references_and_matches(
    session_factory: sessionmaker[Session],
    *,
    article_id: uuid.UUID,
    force_project_id: uuid.UUID | None,
) -> _FirstPassMatchSet:
    with session_factory() as session:
        article = session.get(NewsArticle, article_id)
        if article is None:
            raise RuntimeError("News integration references a missing article.")
        extraction = _current_ok_extraction(session, article)
        if extraction is None:
            return _FirstPassMatchSet(
                article_id=article_id,
                extraction_id=article.current_extraction_id,
                skipped_reason="no_current_ok_extraction",
            )
        references = _references_for_extraction(session, extraction.id)
        if not references:
            return _FirstPassMatchSet(
                article_id=article_id,
                extraction_id=extraction.id,
                skipped_reason="no_references",
                current_extraction_triggered_by=extraction.triggered_by,
            )
        effective_force_project_id, force_project_id_dropped_reason = _effective_force_project_id(
            force_project_id, reference_count=len(references)
        )
        matches = tuple(
            match_news_reference(
                session,
                article=article,
                reference=reference,
                force_project_id=effective_force_project_id,
            )
            for reference in references
        )
        return _FirstPassMatchSet(
            article_id=article_id,
            extraction_id=extraction.id,
            references=tuple(references),
            matches=matches,
            current_extraction_triggered_by=extraction.triggered_by,
            force_project_id_dropped_reason=force_project_id_dropped_reason,
        )


def _should_run_pass3b(first_pass: _FirstPassMatchSet) -> bool:
    if first_pass.current_extraction_triggered_by == PASS3B_TRIGGER_NEW_CANDIDATE:
        return False
    return any(match.status == NewsMatchStatus.NEW_CANDIDATE for match in first_pass.matches)


def _pass3b_context(first_pass: _FirstPassMatchSet) -> dict[str, Any]:
    references = []
    for reference, match in zip(first_pass.references, first_pass.matches, strict=True):
        if match.status != NewsMatchStatus.NEW_CANDIDATE:
            continue
        references.append(
            {
                "reference_id": str(reference.id),
                "reference_index": reference.reference_index,
                "candidate_name": reference.candidate_name,
                "candidate_address": reference.candidate_address,
                "candidate_developer": reference.candidate_developer,
                "candidate_unit_total": reference.candidate_unit_total,
                "candidate_unit_affordable": reference.candidate_unit_affordable,
                "candidate_unit_market_rate": reference.candidate_unit_market_rate,
                "candidate_unit_workforce": reference.candidate_unit_workforce,
                "candidate_confidence": reference.candidate_confidence,
                "match_reason": match.reason,
            }
        )
    return {
        "trigger": PASS3B_TRIGGER_NEW_CANDIDATE,
        "reason": "Matcher returned new_candidate for one or more references.",
        "new_candidate_references": references,
    }


def _run_news_agents_for_first_pass(
    session_factory: sessionmaker[Session],
    *,
    first_pass: _FirstPassMatchSet,
    source_run_id: uuid.UUID | None,
    force_project_id: uuid.UUID | None,
    settings: Settings,
    agent_client: AgentClient | None,
    now: datetime,
) -> dict[uuid.UUID, _NewsAgentDecision]:
    if first_pass.extraction_id is None:
        return {}
    agent_pairs = [
        (reference, match, triggers)
        for reference, match in zip(first_pass.references, first_pass.matches, strict=True)
        if (triggers := _agent_triggers_for_reference(reference=reference, match=match))
    ]
    if not agent_pairs:
        return {}
    client = agent_client
    if settings.agent_enabled_for_news and client is None:
        if not settings.agent_allow_live_llm:
            raise RuntimeError(
                "AGENT_ALLOW_LIVE_LLM=true is required before news integration "
                "constructs a live agent LLM client."
            )
        client = build_anthropic_agent_client(settings=settings, profile=NEWS_AGENT_PROFILE)

    decisions: dict[uuid.UUID, _NewsAgentDecision] = {}
    for reference, match, triggers in agent_pairs:
        with session_factory() as session:
            article = session.get(NewsArticle, first_pass.article_id)
            extraction = session.get(NewsExtraction, first_pass.extraction_id)
            current_reference = session.get(NewsProjectReference, reference.id)
            if article is None or extraction is None or current_reference is None:
                raise RuntimeError("News agent references missing article/extraction/reference.")
            payload = _agent_intake_payload(
                article=article,
                extraction=extraction,
                reference=current_reference,
                match=match,
                force_project_id=force_project_id,
            )
            matcher_payload = _agent_matcher_payload(reference=current_reference, match=match)
        result = run_agent_for_intake(
            IntakeRecord(
                source_type=NEWS_SOURCE_TYPE,
                intake_record_id=str(first_pass.article_id),
                extraction_id=first_pass.extraction_id,
                source_run_id=source_run_id,
                payload=payload,
            ),
            matcher_results=[matcher_payload],
            trigger_reasons=list(triggers),
            profile=NEWS_AGENT_PROFILE,
            client=client,
            settings=settings,
            session_factory=session_factory,
            now=now,
        )
        decisions[reference.id] = _NewsAgentDecision(result=result)
    return decisions


def _agent_triggers_for_reference(
    *,
    reference: NewsProjectReference,
    match: NewsMatchResult,
) -> tuple[AgentTrigger, ...]:
    triggers: list[AgentTrigger] = []
    if match.status == NewsMatchStatus.NEW_CANDIDATE:
        triggers.append(AgentTrigger.NEW_CANDIDATE)
    if match.status == NewsMatchStatus.POSSIBLE and len(match.candidate_project_ids) > 0:
        triggers.append(AgentTrigger.POSSIBLE_MULTI_CANDIDATE)
    if _low_confidence_populated_fields(reference):
        triggers.append(AgentTrigger.LOW_CONFIDENCE)
    return tuple(triggers)


def _low_confidence_populated_fields(reference: NewsProjectReference) -> list[str]:
    if reference.candidate_confidence != "low":
        return []
    fields: list[str] = []
    for field_name, reference_field in LOW_CONFIDENCE_REFERENCE_FIELDS.items():
        if _has_value(serialize_json(getattr(reference, reference_field))):
            fields.append(field_name)
    return fields


def _agent_intake_payload(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    reference: NewsProjectReference,
    match: NewsMatchResult,
    force_project_id: uuid.UUID | None,
) -> dict[str, Any]:
    source = article.source
    return {
        "article": {
            "article_id": str(article.id),
            "title": article.title,
            "url": article.url_canonical,
            "source_slug": source.slug if source is not None else None,
            "published_at": article.published_at.isoformat() if article.published_at else None,
        },
        "extraction": {
            "extraction_id": str(extraction.id),
            "prompt_id": extraction.prompt_id,
            "prompt_version": extraction.prompt_version,
            "triggered_by": extraction.triggered_by,
        },
        "reference": _agent_reference_payload(article=article, reference=reference),
        "matcher": _agent_matcher_payload(reference=reference, match=match),
        "low_confidence_fields": _low_confidence_populated_fields(reference),
        "force_project_id": str(force_project_id) if force_project_id is not None else None,
        "body_access": "Use get_article_body(article_id) if full article text is needed.",
    }


def _agent_reference_payload(
    *,
    article: NewsArticle,
    reference: NewsProjectReference,
) -> dict[str, Any]:
    return {
        "reference_id": str(reference.id),
        "reference_index": reference.reference_index,
        "candidate_name": reference.candidate_name,
        "candidate_address": reference.candidate_address,
        "canonical_address": canonical_address_for_reference(article, reference),
        "candidate_developer": reference.candidate_developer,
        "candidate_unit_total": reference.candidate_unit_total,
        "candidate_unit_affordable": reference.candidate_unit_affordable,
        "candidate_unit_market_rate": reference.candidate_unit_market_rate,
        "candidate_unit_workforce": reference.candidate_unit_workforce,
        "candidate_product_type": reference.candidate_product_type,
        "candidate_age_restriction": reference.candidate_age_restriction,
        "candidate_status_signal": reference.candidate_status_signal,
        "candidate_delivery_year_text": reference.candidate_delivery_year_text,
        "candidate_delivery_year_normalized": serialize_json(
            reference.candidate_delivery_year_normalized
        ),
        "candidate_neighborhood": reference.candidate_neighborhood,
        "candidate_confidence": reference.candidate_confidence,
        "candidate_identifiers": serialize_json(reference.candidate_identifiers or {}),
        "candidate_signal_flags": serialize_json(reference.candidate_signal_flags or {}),
        "mapped_fields": _field_values(_news_extracted_fields(article, reference)),
        "passage_excerpts": _compact_passage_excerpts(reference.passage_excerpts),
    }


def _agent_matcher_payload(
    *,
    reference: NewsProjectReference,
    match: NewsMatchResult,
) -> dict[str, Any]:
    return {
        "reference_id": str(reference.id),
        "reference_index": reference.reference_index,
        "status": match.status.value,
        "match_type": match.match_type,
        "confidence": match.confidence,
        "project_id": str(match.project_id) if match.project_id is not None else None,
        "candidate_project_ids": [str(project_id) for project_id in match.candidate_project_ids],
        "reason": match.reason,
        "candidates": match.candidates_payload(),
    }


def _compact_passage_excerpts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        passage = _clean_text(item.get("passage"))
        if passage is not None and len(passage) > 300:
            passage = passage[:297].rstrip() + "..."
        compact.append(
            {
                "field": item.get("field"),
                "value": serialize_json(item.get("value")),
                "passage": passage,
                "offset_start": item.get("offset_start"),
                "offset_end": item.get("offset_end"),
            }
        )
    return compact


def _integrate_current_extraction(
    session: Session,
    *,
    article_id: uuid.UUID,
    source_run_id: uuid.UUID | None,
    force_project_id: uuid.UUID | None,
    pass3b_triggered: bool,
    pass3b_result: NewsExtractionRunResult | None,
    prior_extraction_id: uuid.UUID | None,
    agent_decisions: dict[uuid.UUID, _NewsAgentDecision],
    now: datetime,
) -> NewsIntegrationResult:
    article = session.get(NewsArticle, article_id)
    if article is None:
        raise RuntimeError("News integration references a missing article.")
    extraction = _current_ok_extraction(session, article)
    if extraction is None:
        return NewsIntegrationResult(
            article_id=article_id,
            source_run_id=source_run_id,
            extraction_id=article.current_extraction_id,
            pass3b_triggered=pass3b_triggered,
            pass3b_result=pass3b_result,
            force_project_id_dropped_reason=None,
            skipped_reason="no_current_ok_extraction",
        )
    source_run = _ensure_source_run(
        session,
        article=article,
        source_run_id=source_run_id,
        now=now,
    )
    references = _references_for_extraction(session, extraction.id)
    _mark_prior_references_superseded(
        session,
        article_id=article.id,
        prior_extraction_id=prior_extraction_id or extraction.supersedes_extraction_id,
        current_extraction_id=extraction.id,
        now=now,
    )
    effective_force_project_id, force_project_id_dropped_reason = _effective_force_project_id(
        force_project_id, reference_count=len(references)
    )
    stats = _MutableIntegrationStats()
    current_evidence_ids: set[uuid.UUID] = set()
    confirmed_by_project: dict[uuid.UUID, _ProjectIntegrationContext] = defaultdict(
        _ProjectIntegrationContext
    )

    for reference in references:
        match = match_news_reference(
            session,
            article=article,
            reference=reference,
            force_project_id=effective_force_project_id,
        )
        agent_decision = agent_decisions.get(reference.id)
        revised_match = _agent_revised_match(
            session,
            match=match,
            agent_decision=agent_decision,
        )
        if revised_match is not None:
            match = revised_match
        _apply_match_to_reference(reference, match, now=now)
        stats.references_processed += 1

        if match.status == NewsMatchStatus.DISCARDED:
            stats.discarded += 1
            continue

        evidence, inserted = _write_news_evidence(
            session,
            article=article,
            extraction=extraction,
            reference=reference,
            match=match,
            project_id=match.project_id if match.status == NewsMatchStatus.CONFIRMED else None,
            now=now,
        )
        if inserted:
            stats.evidence_inserted += 1
        else:
            stats.evidence_reused += 1
        reference.matched_evidence_id = evidence.id
        current_evidence_ids.add(evidence.id)

        if match.status == NewsMatchStatus.CONFIRMED and match.project_id is not None:
            stats.confirmed += 1
            confirmed_by_project[match.project_id].references.append(
                _ConfirmedReference(
                    reference=reference,
                    match=match,
                    evidence=evidence,
                    agent_run_id=(
                        agent_decision.result.agent_run_id if agent_decision is not None else None
                    ),
                )
            )
            continue

        if match.status == NewsMatchStatus.POSSIBLE:
            stats.possible += 1
            item, created = _upsert_discovery_review_item(
                session,
                article=article,
                source_run=source_run,
                extraction=extraction,
                reference=reference,
                match=match,
                evidence=evidence,
                item_type=ReviewItemType.POSSIBLE_MATCH,
            )
            reference.review_item_id = item.id
            if agent_decision is not None:
                _link_agent_run_review_item(
                    session,
                    agent_run_id=agent_decision.result.agent_run_id,
                    review_item_id=item.id,
                )
            _count_review_item(stats, created)
            continue

        if match.status == NewsMatchStatus.NEW_CANDIDATE:
            stats.new_candidate += 1
            source_run.new_candidates += 1
            item, created = _upsert_discovery_review_item(
                session,
                article=article,
                source_run=source_run,
                extraction=extraction,
                reference=reference,
                match=match,
                evidence=evidence,
                item_type=ReviewItemType.NEW_CANDIDATE,
            )
            reference.review_item_id = item.id
            if agent_decision is not None:
                _link_agent_run_review_item(
                    session,
                    agent_run_id=agent_decision.result.agent_run_id,
                    review_item_id=item.id,
                )
            _count_review_item(stats, created)

    session.flush()
    _supersede_stale_article_evidence(
        session,
        article=article,
        current_evidence_ids=current_evidence_ids,
        now=now,
    )
    session.flush()
    for project_id, context in confirmed_by_project.items():
        stats.status_change_review_items += _integrate_confirmed_project(
            session,
            project_id=project_id,
            source_run=source_run,
            article=article,
            extraction=extraction,
            context=context,
        )

    source_run.new_matches += stats.confirmed
    source_run.updates_found += stats.status_change_review_items
    if source_run.rows_inserted is None:
        source_run.rows_inserted = 0
    if source_run.rows_updated is None:
        source_run.rows_updated = 0
    source_run.rows_inserted += stats.evidence_inserted
    source_run.rows_updated += stats.status_change_review_items
    session.flush()
    return NewsIntegrationResult(
        article_id=article_id,
        source_run_id=source_run.id,
        extraction_id=extraction.id,
        references_processed=stats.references_processed,
        confirmed=stats.confirmed,
        possible=stats.possible,
        new_candidate=stats.new_candidate,
        discarded=stats.discarded,
        evidence_inserted=stats.evidence_inserted,
        evidence_reused=stats.evidence_reused,
        review_items_created=stats.review_items_created,
        review_items_updated=stats.review_items_updated,
        status_change_review_items=stats.status_change_review_items,
        pass3b_triggered=pass3b_triggered,
        pass3b_result=pass3b_result,
        force_project_id_dropped_reason=force_project_id_dropped_reason,
    )


def _effective_force_project_id(
    force_project_id: uuid.UUID | None,
    *,
    reference_count: int,
) -> tuple[uuid.UUID | None, str | None]:
    if force_project_id is None:
        return None, None
    if reference_count == 1:
        return force_project_id, None
    return None, "multi_reference"


def _current_ok_extraction(session: Session, article: NewsArticle) -> NewsExtraction | None:
    if article.current_extraction_id is None:
        return None
    extraction = session.get(NewsExtraction, article.current_extraction_id)
    if extraction is None or extraction.parse_status != NewsExtractionParseStatus.OK.value:
        return None
    return extraction


def _references_for_extraction(
    session: Session,
    extraction_id: uuid.UUID,
) -> list[NewsProjectReference]:
    return (
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


def _ensure_source_run(
    session: Session,
    *,
    article: NewsArticle,
    source_run_id: uuid.UUID | None,
    now: datetime,
) -> SourceRun:
    if source_run_id is not None:
        source_run = session.get(SourceRun, source_run_id)
        if source_run is not None:
            return source_run
    source = article.source
    source_run = SourceRun(
        market=source.market.slug if source.market is not None else "unscoped",
        jurisdiction_id=source.jurisdiction_id,
        source_name=source.slug,
        collection_mode="single",
        trigger_type=ScrapeTriggerType.USER_INITIATED.value,
        initiated_by_user_id=article.ingested_by_user_id,
        finished_at=now,
        records_pulled=1,
        rows_updated=0,
    )
    session.add(source_run)
    session.flush()
    return source_run


def _apply_match_to_reference(
    reference: NewsProjectReference,
    match: NewsMatchResult,
    *,
    now: datetime,
) -> None:
    reference.match_status = match.status.value
    reference.matched_project_id = match.project_id
    reference.match_confidence = match.confidence
    reference.match_reason = match.reason
    reference.match_candidates = match.candidates_payload()
    reference.match_decision_at = now


def _agent_revised_match(
    session: Session,
    *,
    match: NewsMatchResult,
    agent_decision: _NewsAgentDecision | None,
) -> NewsMatchResult | None:
    promoted_match = _agent_promoted_match(
        session,
        match=match,
        agent_decision=agent_decision,
    )
    if promoted_match is not None:
        return promoted_match
    return _agent_confirmed_possible_match(
        session,
        match=match,
        agent_decision=agent_decision,
    )


def _agent_promoted_match(
    session: Session,
    *,
    match: NewsMatchResult,
    agent_decision: _NewsAgentDecision | None,
) -> NewsMatchResult | None:
    if (
        match.status not in {NewsMatchStatus.NEW_CANDIDATE, NewsMatchStatus.DISCARDED}
        or agent_decision is None
    ):
        return None
    result = agent_decision.result
    if result.outcome != AgentRunOutcome.COMPLETED.value:
        return None
    verdict = result.agent_revised_verdict
    if not isinstance(verdict, dict):
        return None
    if verdict.get("decision") != AGENT_PROMOTE_EXISTING_PROJECT_DECISION:
        return None
    project_id = _uuid_from_agent_verdict(verdict.get("project_id"))
    if project_id is None or session.get(Project, project_id) is None:
        return None
    confidence = _agent_confidence(verdict.get("confidence"))
    if confidence is None:
        return None
    # A new_candidate has no deterministic candidate set; the agent-selected
    # project becomes the single audited attribution candidate.
    return NewsMatchResult(
        status=NewsMatchStatus.CONFIRMED,
        match_type=AGENT_PROMOTED_EXISTING_PROJECT_MATCH,
        confidence=confidence,
        project_id=project_id,
        candidate_project_ids=[project_id],
        reason=(
            f"Agent promoted deterministic {match.status.value} to an existing project "
            f"after tool review; agent_run_id={result.agent_run_id}."
        ),
        diagnostics={"agent_run_id": str(result.agent_run_id)},
    )


def _agent_confirmed_possible_match(
    session: Session,
    *,
    match: NewsMatchResult,
    agent_decision: _NewsAgentDecision | None,
) -> NewsMatchResult | None:
    if match.status != NewsMatchStatus.POSSIBLE or agent_decision is None:
        return None
    result = agent_decision.result
    if result.outcome != AgentRunOutcome.COMPLETED.value:
        return None
    verdict = result.agent_revised_verdict
    if not isinstance(verdict, dict):
        return None
    if verdict.get("decision") != AGENT_CONFIRM_EXISTING_PROJECT_DECISION:
        return None
    project_id = _uuid_from_agent_verdict(verdict.get("project_id"))
    if project_id is None or project_id not in set(match.candidate_project_ids):
        return None
    if session.get(Project, project_id) is None:
        return None
    confidence = _agent_confidence(verdict.get("confidence"))
    if confidence is None:
        return None
    # Type 3 chooses from the matcher candidates; preserve the full original
    # candidate set so audit can show what the agent selected from.
    return NewsMatchResult(
        status=NewsMatchStatus.CONFIRMED,
        match_type=AGENT_CONFIRMED_POSSIBLE_MATCH,
        confidence=confidence,
        project_id=project_id,
        candidate_project_ids=list(match.candidate_project_ids),
        candidates=list(match.candidates),
        reason=(
            "Agent confirmed one deterministic possible-match candidate after "
            f"tool review; agent_run_id={result.agent_run_id}."
        ),
        diagnostics={"agent_run_id": str(result.agent_run_id)},
    )


def _uuid_from_agent_verdict(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _agent_confidence(value: Any) -> float | None:
    if value in (None, ""):
        # High confidence band, but not near-certainty; the agent supplied no score.
        return 0.93
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0 or confidence > 1:
        return None
    return confidence


def _link_agent_run_review_item(
    session: Session,
    *,
    agent_run_id: uuid.UUID,
    review_item_id: uuid.UUID,
) -> None:
    session.execute(
        insert(AgentRunReviewItem)
        .values(agent_run_id=agent_run_id, review_item_id=review_item_id)
        .on_conflict_do_nothing()
    )


def _write_news_evidence(
    session: Session,
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    reference: NewsProjectReference,
    match: NewsMatchResult,
    project_id: uuid.UUID | None,
    now: datetime,
) -> tuple[Evidence, bool]:
    if reference.matched_evidence_id is not None:
        existing = session.get(Evidence, reference.matched_evidence_id)
        if existing is not None:
            if project_id is not None and existing.project_id is None:
                existing.project_id = project_id
            return existing, False

    raw_data = _news_raw_data(article=article, extraction=extraction, reference=reference)
    extracted_fields = _news_extracted_fields(article, reference)
    write_result = write_evidence(
        session,
        project_id=project_id,
        source_name=article.source.slug,
        source_record_id=str(reference.id),
        raw_data=raw_data,
        mapped_fields=_field_values(extracted_fields),
        extracted_fields=extracted_fields,
        collected_at=now,
        ingest_method=_ingest_method(article, extraction),
        evidence_date=_article_evidence_date(article, now=now),
        notes=None,
    )
    evidence = write_result.evidence
    if evidence is None:
        raise RuntimeError("News evidence write did not return an evidence row.")
    evidence.signal_flags = reference.candidate_signal_flags or None
    session.flush()
    return evidence, write_result.inserted


def _news_raw_data(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    reference: NewsProjectReference,
) -> dict[str, Any]:
    source = article.source
    return {
        "article_id": str(article.id),
        "extraction_id": str(extraction.id),
        "reference_id": str(reference.id),
        "reference_index": reference.reference_index,
        "publication": source.name,
        "publisher": source.slug,
        "source_name": source.name,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "author": article.byline_author,
        "url": article.url_canonical,
        "title": article.title,
        "body_excerpt": (article.body_text or "")[:600],
        "prompt_id": extraction.prompt_id,
        "prompt_version": extraction.prompt_version,
        "match_status": reference.match_status,
        "match_confidence": reference.match_confidence,
    }


def _news_extracted_fields(
    article: NewsArticle,
    reference: NewsProjectReference,
) -> dict[str, dict[str, Any]]:
    confidence = reference.candidate_confidence
    fields: dict[str, Any] = {
        "project_name": reference.candidate_name,
        "canonical_address": canonical_address_for_reference(article, reference),
        "developer": reference.candidate_developer,
        "total_units": reference.candidate_unit_total,
        "affordable_units": reference.candidate_unit_affordable,
        "market_rate_units": reference.candidate_unit_market_rate,
        "workforce_units": reference.candidate_unit_workforce,
        "product_type": _product_type_value(reference.candidate_product_type),
        "age_restriction": _age_restriction_value(reference.candidate_age_restriction),
        "pipeline_status": reference.candidate_status_signal,
        "date_delivery": reference.candidate_delivery_year_normalized,
    }
    highlights_by_field = _highlights_by_project_field(reference)
    wrapped: dict[str, dict[str, Any]] = {}
    for field_name, value in fields.items():
        serialized = serialize_json(value)
        if not _has_value(serialized):
            continue
        wrapped[field_name] = {
            "value": serialized,
            "confidence": confidence,
        }
        highlights = highlights_by_field.get(field_name)
        if highlights:
            wrapped[field_name]["highlights"] = highlights
    return wrapped


def _highlights_by_project_field(
    reference: NewsProjectReference,
) -> dict[str, list[dict[str, Any]]]:
    highlights: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excerpts = reference.passage_excerpts or []
    if not isinstance(excerpts, list):
        return {}
    for excerpt in excerpts:
        if not isinstance(excerpt, dict):
            continue
        reference_field = str(excerpt.get("field") or "")
        project_field = REFERENCE_FIELD_TO_PROJECT_FIELD.get(reference_field)
        if project_field is None:
            project_field = reference_field
        highlights[project_field].append(
            {
                "field": project_field,
                "value": serialize_json(excerpt.get("value")),
                "passage": excerpt.get("passage"),
                "offset_start": excerpt.get("offset_start"),
                "offset_end": excerpt.get("offset_end"),
            }
        )
    return dict(highlights)


def _field_values(extracted_fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        field_name: payload.get("value")
        for field_name, payload in extracted_fields.items()
        if isinstance(payload, dict)
    }


def _article_evidence_date(article: NewsArticle, *, now: datetime) -> date:
    if article.published_at is not None:
        return article.published_at.date()
    if article.fetched_at is not None:
        return article.fetched_at.date()
    return now.date()


def _ingest_method(article: NewsArticle, extraction: NewsExtraction) -> str:
    if extraction.pass_name == NewsExtractionPass.REEXTRACTION.value:
        return "news_reextraction"
    return article.ingest_method or "news_paste_a_link"


def _supersede_stale_article_evidence(
    session: Session,
    *,
    article: NewsArticle,
    current_evidence_ids: set[uuid.UUID],
    now: datetime,
) -> None:
    statement = select(Evidence).where(
        Evidence.source_type == NEWS_SOURCE_TYPE,
        Evidence.superseded_at.is_(None),
        Evidence.raw_data["article_id"].astext == str(article.id),
    )
    if current_evidence_ids:
        statement = statement.where(Evidence.id.notin_(sorted(current_evidence_ids, key=str)))
    stale_rows = session.execute(statement).scalars().all()
    for stale in stale_rows:
        stale.superseded_at = now


def _mark_prior_references_superseded(
    session: Session,
    *,
    article_id: uuid.UUID,
    prior_extraction_id: uuid.UUID | None,
    current_extraction_id: uuid.UUID,
    now: datetime,
) -> None:
    _ = prior_extraction_id
    prior_references = (
        session.execute(
            select(NewsProjectReference).where(
                NewsProjectReference.article_id == article_id,
                NewsProjectReference.extraction_id != current_extraction_id,
            )
        )
        .scalars()
        .all()
    )
    for reference in prior_references:
        reference.match_status = NewsMatchStatus.SUPERSEDED_BY_REEXTRACTION.value
        reference.match_reason = (
            "Reference was superseded because Pass 3b advanced the article's current extraction."
        )
        reference.match_decision_at = now


def _integrate_confirmed_project(
    session: Session,
    *,
    project_id: uuid.UUID,
    source_run: SourceRun,
    article: NewsArticle,
    extraction: NewsExtraction,
    context: _ProjectIntegrationContext,
) -> int:
    project = session.get(Project, project_id)
    if project is None:
        return 0
    previous_snapshot = snapshot_project_for_diff(project)
    resolution_result = resolve_project(project_id, session, apply=True)
    session.flush()
    current_snapshot = snapshot_project_for_diff(project)
    diff_result = diff_project_snapshots(
        previous_snapshot,
        current_snapshot,
        status_evidence_type=_status_evidence_type_from_resolution(resolution_result),
        status_evidence_date=_status_evidence_date_from_resolution(resolution_result),
        status_reason=_status_reason_from_resolution(resolution_result),
        review_flags=list(resolution_result.review_flags),
    )
    if not diff_result.has_reviewable_changes:
        return 0
    return _upsert_status_change_review_items(
        session,
        project=project,
        source_run=source_run,
        article=article,
        extraction=extraction,
        context=context,
        diff_result=diff_result,
        resolution_result=resolution_result,
    )


def _upsert_status_change_review_items(
    session: Session,
    *,
    project: Project,
    source_run: SourceRun,
    article: NewsArticle,
    extraction: NewsExtraction,
    context: _ProjectIntegrationContext,
    diff_result: DiffResult,
    resolution_result,
) -> int:
    created_count = 0
    for field_name in _review_item_fields(diff_result):
        field_changes = [
            change for change in diff_result.field_changes if change.field == field_name
        ]
        field_flags = _review_flags_for_field(diff_result.review_flags, field_name)
        field_context = _field_reference_context(
            field_name=field_name,
            context=context,
            resolution_result=resolution_result,
        )
        if field_context["reference"] is None:
            continue
        payload = {
            "match": field_context["match"].candidates_payload()
            if field_context["match"] is not None
            else None,
            "field_name": field_name,
            "source_record_id": str(field_context["reference"].id),
            "canonical_address": canonical_address_for_reference(
                article,
                field_context["reference"],
            ),
            "mapped_fields": _field_values(
                _news_extracted_fields(article, field_context["reference"])
            ),
            "changes": [
                _serialize_change(
                    change,
                    evidence_id=field_context["winning_evidence_id"],
                )
                for change in field_changes
            ],
            "review_flags": [_serialize_review_flag(flag) for flag in field_flags],
            "status_suggestion": (
                _serialize_status_suggestion(diff_result.status_suggestion)
                if field_name == "pipeline_status"
                else None
            ),
            "current_value": serialize_json(getattr(project, field_name, None)),
            "evidence_ids": [str(evidence_id) for evidence_id in field_context["evidence_ids"]],
            "rule_applied": field_context["rule_applied"],
            "resolution_confidence": field_context["resolution_confidence"],
            "resolution_winning_evidence_id": (
                str(field_context["resolution_winning_evidence_id"])
                if field_context["resolution_winning_evidence_id"] is not None
                else None
            ),
            "news_context": _news_context(
                article=article,
                extraction=extraction,
                reference=field_context["reference"],
                field_name=field_name,
                evidence_id=field_context["winning_evidence_id"],
            ),
        }
        proposed_value = proposed_value_for_payload(payload, field_name)
        if not payload["evidence_ids"] and field_context["winning_evidence_id"] is not None:
            payload["evidence_ids"] = [str(field_context["winning_evidence_id"])]
        review_item, created = upsert_decision_card_review_item(
            session,
            project_id=project.id,
            source_run_id=source_run.id,
            item_type=ReviewItemType.STATUS_CHANGE,
            field_name=field_name,
            priority=_priority_for_field(diff_result, field_name, field_context["reference"]),
            match_confidence=field_context["match"].confidence
            if field_context["match"] is not None
            else None,
            payload=payload,
            proposed_value=proposed_value,
            winning_evidence_id=field_context["winning_evidence_id"],
        )
        if field_context["reference"] is not None:
            field_context["reference"].review_item_id = review_item.id
        if field_context["agent_run_id"] is not None:
            _link_agent_run_review_item(
                session,
                agent_run_id=field_context["agent_run_id"],
                review_item_id=review_item.id,
            )
        if created:
            created_count += 1
    return created_count


def _field_reference_context(
    *,
    field_name: str,
    context: _ProjectIntegrationContext,
    resolution_result,
) -> dict[str, Any]:
    field_resolution = resolution_result.field_resolutions.get(field_name)
    evidence_ids = list(field_resolution.evidence_ids) if field_resolution is not None else []
    resolution_winning_evidence_id = evidence_ids[0] if evidence_ids else None
    selected = None
    for candidate in context.references:
        if candidate.evidence.id in evidence_ids:
            selected = candidate
            break
    winning_evidence_id = selected.evidence.id if selected is not None else None
    return {
        "reference": selected.reference if selected is not None else None,
        "match": selected.match if selected is not None else None,
        "evidence_ids": evidence_ids,
        "winning_evidence_id": winning_evidence_id,
        "agent_run_id": selected.agent_run_id if selected is not None else None,
        "resolution_winning_evidence_id": resolution_winning_evidence_id,
        "rule_applied": field_resolution.rule_applied if field_resolution is not None else None,
        "resolution_confidence": (
            field_resolution.confidence.value if field_resolution is not None else None
        ),
    }


def _upsert_discovery_review_item(
    session: Session,
    *,
    article: NewsArticle,
    source_run: SourceRun,
    extraction: NewsExtraction,
    reference: NewsProjectReference,
    match: NewsMatchResult,
    evidence: Evidence,
    item_type: ReviewItemType,
) -> tuple[ReviewItem, bool]:
    existing = _find_existing_discovery_review_item(session, reference=reference)
    payload = _discovery_payload(
        article=article,
        extraction=extraction,
        reference=reference,
        match=match,
        evidence=evidence,
    )
    if existing is not None:
        existing.source_run_id = source_run.id
        existing.priority = _discovery_priority(reference, item_type)
        existing.match_confidence = match.confidence
        existing.winning_evidence_id = evidence.id
        existing.payload = payload
        return existing, False
    item = ReviewItem(
        project_id=None,
        source_run_id=source_run.id,
        item_type=item_type,
        status=ReviewItemStatus.OPEN,
        priority=_discovery_priority(reference, item_type),
        match_confidence=match.confidence,
        winning_evidence_id=evidence.id,
        payload=payload,
    )
    session.add(item)
    session.flush()
    return item, True


def _find_existing_discovery_review_item(
    session: Session,
    *,
    reference: NewsProjectReference,
) -> ReviewItem | None:
    if reference.review_item_id is not None:
        existing = session.get(ReviewItem, reference.review_item_id)
        if existing is not None and existing.state in ACTIVE_REVIEW_STATES:
            return existing
    source_record_id = str(reference.id)
    rows = (
        session.execute(
            select(ReviewItem).where(
                ReviewItem.item_type.in_(
                    [ReviewItemType.POSSIBLE_MATCH, ReviewItemType.NEW_CANDIDATE]
                ),
                ReviewItem.state.in_(ACTIVE_REVIEW_STATES),
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if payload.get("source_record_id") == source_record_id:
            return item
    return None


def _discovery_payload(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    reference: NewsProjectReference,
    match: NewsMatchResult,
    evidence: Evidence,
) -> dict[str, Any]:
    canonical_address = canonical_address_for_reference(article, reference)
    mapped_fields = _mapped_fields_for_review_payload(article, reference, canonical_address)
    return {
        "match": match.candidates_payload(),
        "candidate_project_ids": [str(project_id) for project_id in match.candidate_project_ids],
        "source_record_id": str(reference.id),
        "canonical_address": canonical_address,
        "raw_addresses": _raw_addresses(reference, canonical_address),
        "identifiers": reference.candidate_identifiers or {},
        "mapped_fields": mapped_fields,
        "raw_payload": _news_raw_data(article=article, extraction=extraction, reference=reference),
        "evidence_ids": [str(evidence.id)],
        "winning_evidence_id": str(evidence.id),
        "news_context": _news_context(
            article=article,
            extraction=extraction,
            reference=reference,
            field_name=None,
            evidence_id=evidence.id,
        ),
    }


def _mapped_fields_for_review_payload(
    article: NewsArticle,
    reference: NewsProjectReference,
    canonical_address: str | None,
) -> dict[str, Any]:
    source = article.source
    jurisdiction = source.jurisdiction
    market = source.market
    config = source.config if isinstance(source.config, dict) else {}
    city = _clean_text(config.get("default_city"))
    if city is None and jurisdiction is not None and jurisdiction.entity_type == "city":
        city = _clean_text(jurisdiction.display_name or jurisdiction.name)
    state = _clean_text(config.get("default_state"))
    if state is None and jurisdiction is not None and jurisdiction.state != "NA":
        state = jurisdiction.state
    if state is None and market is not None and market.state != "NA":
        state = market.state
    normalized_address = _normalized_reference_address(article, reference)
    if city is None and normalized_address is not None:
        city = normalized_address.city
    if state is None and normalized_address is not None:
        state = normalized_address.state
    county = _clean_text(config.get("default_county"))
    if county is None and market is not None:
        county = _county_from_market_name(market.name)
    mapped = {
        "canonical_address": canonical_address,
        "project_name": reference.candidate_name,
        "city": city,
        "state": state,
        "county": county,
        "zip": (
            normalized_address.postal_code
            if normalized_address is not None and normalized_address.postal_code is not None
            else _zip_from_canonical_address(canonical_address)
        ),
    }
    mapped.update(_field_values(_news_extracted_fields(article, reference)))
    return {key: value for key, value in mapped.items() if _has_value(value)}


def _normalized_reference_address(article: NewsArticle, reference: NewsProjectReference):
    raw_address = _clean_text(reference.candidate_address)
    if raw_address is None:
        return None
    source = article.source
    jurisdiction = source.jurisdiction
    market = source.market
    config = source.config if isinstance(source.config, dict) else {}
    city = _clean_text(config.get("default_city"))
    if city is None and jurisdiction is not None and jurisdiction.entity_type == "city":
        city = _clean_text(jurisdiction.display_name or jurisdiction.name)
    state = _clean_text(config.get("default_state"))
    if state is None and jurisdiction is not None and jurisdiction.state != "NA":
        state = jurisdiction.state
    if state is None and market is not None and market.state != "NA":
        state = market.state
    return normalize_address(
        raw_address,
        city=city,
        state=state,
        market=market.slug if market is not None else None,
    )


def _raw_addresses(
    reference: NewsProjectReference,
    canonical_address: str | None,
) -> list[str]:
    addresses = [
        reference.candidate_address,
        canonical_address,
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for address in addresses:
        text = _clean_text(address)
        if text is None or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _news_context(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    reference: NewsProjectReference | None,
    field_name: str | None,
    evidence_id: uuid.UUID | None,
) -> dict[str, Any]:
    structural_disagreement = _structural_disagreement(article, field_name)
    return {
        "article_id": str(article.id),
        "extraction_id": str(extraction.id),
        "reference_id": str(reference.id) if reference is not None else None,
        "reference_index": reference.reference_index if reference is not None else None,
        "extraction_confidence": (
            reference.candidate_confidence if reference is not None else None
        ),
        "structural_disagreement": structural_disagreement,
        "extraction_version": article.current_extraction_version,
        "prompt_id": extraction.prompt_id,
        "prompt_version": extraction.prompt_version,
        "evidence_id": str(evidence_id) if evidence_id is not None else None,
        "article_title": article.title,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "url": article.url_canonical,
    }


def _structural_disagreement(
    article: NewsArticle,
    field_name: str | None,
) -> dict[str, Any] | None:
    if field_name is None:
        return None
    structural = article.structural_signals if isinstance(article.structural_signals, dict) else {}
    signals = structural.get("signals")
    if not isinstance(signals, list):
        return None
    extractors_by_field = {
        "total_units": {"unit_count"},
        "developer": {"developer_dict"},
        "pipeline_status": {"status_phrase"},
        "date_delivery": {"delivery_phrase"},
        "product_type": {"product_type"},
        "age_restriction": {"age_restriction"},
        "affordable_units": {"affordable_split_phrase"},
        "market_rate_units": {"affordable_split_phrase"},
        "workforce_units": {"affordable_split_phrase"},
    }
    wanted = extractors_by_field.get(field_name, set())
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        extractor = signal.get("extractor")
        if extractor not in wanted:
            continue
        if extractor == "affordable_split_phrase" and not _split_signal_matches_field(
            signal,
            field_name,
        ):
            continue
        if extractor in wanted:
            return {
                "extractor": signal.get("extractor"),
                "raw_match": signal.get("raw_match"),
                "canonical": serialize_json(signal.get("canonical")),
            }
    return None


def _split_signal_matches_field(signal: dict[str, Any], field_name: str) -> bool:
    structural = signal.get("canonical")
    if not isinstance(structural, dict):
        return False
    kind = str(structural.get("kind") or "")
    if field_name == "workforce_units":
        return kind == "workforce"
    if field_name == "market_rate_units":
        return kind == "market_rate"
    if field_name == "affordable_units":
        return kind in {"affordable", "low_income", "moderate_income"}
    return False


def _review_item_fields(diff_result: DiffResult) -> list[str]:
    fields: list[str] = []
    if diff_result.status_suggestion is not None:
        fields.append("pipeline_status")
    fields.extend(change.field for change in diff_result.field_changes)
    fields.extend(_field_for_review_flag(flag) for flag in diff_result.review_flags)
    return _dedupe(field for field in fields if field)


def _field_for_review_flag(review_flag: ReviewFlag) -> str:
    if review_flag.code in {
        "status_transition_requires_review",
        "permit_issued_requires_review",
    }:
        return "pipeline_status"
    if review_flag.code == "unit_split_mismatch":
        return "total_units"
    if review_flag.code in {
        "developer_canonicalization_review",
        "developer_registry_new_name",
    }:
        return "developer"
    raise ValueError(f"Unknown review flag code for news integration: {review_flag.code}")


def _review_flags_for_field(
    review_flags: list[ReviewFlag],
    field_name: str,
) -> list[ReviewFlag]:
    return [flag for flag in review_flags if _field_for_review_flag(flag) == field_name]


def _priority_for_field(
    diff_result: DiffResult,
    field_name: str,
    reference: NewsProjectReference | None,
) -> Priority:
    if reference is not None and reference.candidate_confidence == "low":
        return Priority.LOW
    if diff_result.status_suggestion is not None and field_name == "pipeline_status":
        return diff_result.status_suggestion.priority
    for change in diff_result.field_changes:
        if change.field == field_name:
            return change.priority
    for flag in _review_flags_for_field(diff_result.review_flags, field_name):
        return flag.priority
    return Priority.MEDIUM


def _discovery_priority(
    reference: NewsProjectReference,
    item_type: ReviewItemType,
) -> Priority:
    if item_type == ReviewItemType.POSSIBLE_MATCH:
        return Priority.MEDIUM
    total_units = reference.candidate_unit_total
    if isinstance(total_units, int) and total_units >= 100:
        return Priority.HIGH
    if isinstance(total_units, int) and total_units >= 25:
        return Priority.MEDIUM
    return Priority.LOW


def _count_review_item(stats: _MutableIntegrationStats, created: bool) -> None:
    if created:
        stats.review_items_created += 1
    else:
        stats.review_items_updated += 1


def _status_evidence_type_from_resolution(resolution_result) -> str | None:
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    evidence_type = status_resolution.metadata.get("evidence_type")
    return _clean_text(evidence_type)


def _status_evidence_date_from_resolution(resolution_result) -> date | None:
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    return status_resolution.evidence_date


def _status_reason_from_resolution(resolution_result) -> str | None:
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    return _clean_text(status_resolution.metadata.get("review_reason"))


def _serialize_change(
    change: DetectedChange,
    *,
    evidence_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "field": change.field,
        "field_name": change.field,
        "old_value": serialize_json(change.old_value),
        "new_value": serialize_json(change.new_value),
        "priority": change.priority.value,
        "source": NEWS_SOURCE_TYPE,
        "evidence_id": str(evidence_id) if evidence_id is not None else None,
    }


def _serialize_review_flag(review_flag: ReviewFlag) -> dict[str, Any]:
    return {
        "code": review_flag.code,
        "message": review_flag.message,
        "priority": review_flag.priority.value,
    }


def _serialize_status_suggestion(suggestion) -> dict[str, Any] | None:
    if suggestion is None:
        return None
    return {
        "current_status": (
            suggestion.current_status.value if suggestion.current_status is not None else None
        ),
        "suggested_status": suggestion.suggested_status.value,
        "evidence_type": suggestion.evidence_type,
        "evidence_date": serialize_json(suggestion.evidence_date),
        "reason": suggestion.reason,
        "priority": suggestion.priority.value,
        "rule_code": suggestion.rule_code,
        "proof_level": suggestion.proof_level,
    }


def _product_type_value(value: str | None) -> str | None:
    normalized = _clean_text(value)
    if normalized is None:
        return None
    return {
        "apartment": ProductType.APARTMENT.value,
        "condo": ProductType.CONDO.value,
        "townhome": ProductType.TOWNHOME.value,
        "single_family": ProductType.SINGLE_FAMILY.value,
        "micro_co_living": ProductType.MICRO_CO_LIVING.value,
        "other": ProductType.OTHER.value,
    }.get(normalized)


def _age_restriction_value(value: str | None) -> str | None:
    normalized = _clean_text(value)
    if normalized is None:
        return None
    return {
        "non_age_restricted": AgeRestriction.NON_AGE_RESTRICTED.value,
        "senior": AgeRestriction.SENIOR.value,
        "student": AgeRestriction.STUDENT.value,
        "unknown": AgeRestriction.UNKNOWN.value,
    }.get(normalized)


def _county_from_market_name(value: str | None) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    suffix = " County"
    if text.endswith(suffix):
        return text[: -len(suffix)]
    return text


def _zip_from_canonical_address(value: str | None) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    suffix = text.rsplit(" ", 1)[-1]
    return suffix if suffix.isdigit() and len(suffix) == 5 else None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _dedupe(values) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
