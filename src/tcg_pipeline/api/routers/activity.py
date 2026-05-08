from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.api.schemas import (
    ActivityArticleSummary,
    ActivityEventResponse,
    ActivityFeedResponse,
    ActivityProjectSummary,
    ActivitySemanticMetricResponse,
    ActivitySemanticMetricsResponse,
)
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunReviewItem,
    ChangeLog,
    NewsArticle,
    NewsExtractionParseStatus,
    NewsProjectReference,
    NewsSemanticInterpretation,
    NewsSource,
    Project,
    ResolutionLog,
)

router = APIRouter(prefix="/activity", tags=["activity"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)

EVENT_TYPES = {"change", "resolution", "agent", "semantic"}
VIEW_PRESETS = {"all", "agent", "auto_applied", "semantic"}
AGENT_FAILURE_OUTCOMES = {"failed_timeout", "failed_budget", "failed_error", "killed_by_switch"}
AGENT_FAILURE_DISPLAY = {
    "failed_timeout": "Agent failed: Timeout",
    "failed_budget": "Agent failed: Over budget",
    "failed_error": "Agent failed: Error",
    "killed_by_switch": "Agent killed by switch",
}
SEMANTIC_LOGICAL_SOURCE = "semantic.news_v1"
SEMANTIC_SOURCE_LABEL = "Semantic Pass 2c"
SEMANTIC_GAP_RATE_THRESHOLD = 0.15
SEMANTIC_UNMAPPABLE_RATE_THRESHOLD = 0.05
SEMANTIC_REJECTION_SIGMA_THRESHOLD = 2.0
MAX_INTERNAL_LIMIT = 500
MAX_SEMANTIC_METRIC_ROWS = 5000


@router.get("/events")
def list_activity_events(
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    event_type: Annotated[str | None, Query(max_length=40)] = None,
    view: Annotated[str, Query(max_length=40)] = "all",
    source: Annotated[str | None, Query(max_length=120)] = None,
    field: Annotated[str | None, Query(max_length=120)] = None,
    actor: Annotated[str | None, Query(max_length=200)] = None,
    project_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_INTERNAL_LIMIT)] = 200,
) -> ActivityFeedResponse:
    del user
    normalized_type = event_type if event_type in EVENT_TYPES else None
    normalized_view = view if view in VIEW_PRESETS else "all"
    events: list[ActivityEventResponse] = []
    if normalized_type in (None, "change"):
        events.extend(
            _change_events(
                session,
                source=source,
                field=field,
                actor=actor,
                project_id=project_id,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
            )
        )
    if normalized_type in (None, "resolution"):
        events.extend(
            _resolution_events(
                session,
                source=source,
                field=field,
                project_id=project_id,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
            )
        )
    if normalized_type in (None, "agent"):
        events.extend(
            _agent_events(
                session,
                source=source,
                actor=actor,
                project_id=project_id,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
            )
        )
    if normalized_type in (None, "semantic"):
        events.extend(
            _semantic_events(
                session,
                source=source,
                field=field,
                project_id=project_id,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
            )
        )

    filtered_events = [event for event in events if _event_matches_view(event, normalized_view)]
    filtered_events.sort(key=_event_sort_key, reverse=True)
    return ActivityFeedResponse(
        generated_at=datetime.now(UTC).isoformat(),
        events=filtered_events[:limit],
    )


@router.get("/semantic-metrics")
def list_activity_semantic_metrics(
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    source: Annotated[str | None, Query(max_length=120)] = None,
    field: Annotated[str | None, Query(max_length=120)] = None,
    market: Annotated[str | None, Query(max_length=120)] = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> ActivitySemanticMetricsResponse:
    del user
    rows = _semantic_rows(
        session,
        source=source,
        from_date=from_date,
        to_date=to_date,
        limit=MAX_SEMANTIC_METRIC_ROWS,
    )
    articles = _news_articles_by_id(session, [row.article_id for row in rows])
    source_names = _news_sources_by_id(
        session,
        [
            article.news_source_id
            for article in articles.values()
            if article.news_source_id is not None
        ],
    )
    references_by_extraction = _semantic_references_by_extraction(
        session,
        [row.extraction_id for row in rows],
    )
    semantic_items: list[tuple[NewsSemanticInterpretation, dict[str, Any], uuid.UUID | None]] = []
    for row in rows:
        references = references_by_extraction.get(row.extraction_id, [])
        for interpretation in _semantic_payloads(row):
            project_id = _semantic_project_id_for_interpretation(interpretation, references)
            semantic_items.append((row, interpretation, project_id))
    projects = _projects_by_id(session, [project_id for _, _, project_id in semantic_items])
    total: Counter[tuple[str | None, str | None, str | None, str, str]] = Counter()
    gaps: Counter[tuple[str | None, str | None, str | None, str, str]] = Counter()
    unmappable: Counter[tuple[str | None, str | None, str | None, str, str]] = Counter()
    for row, interpretation, project_id in semantic_items:
        article = articles.get(row.article_id)
        source_row = (
            source_names.get(article.news_source_id)
            if article is not None and article.news_source_id is not None
            else None
        )
        project = projects.get(project_id) if project_id is not None else None
        if market and (project is None or project.market != market):
            continue
        field_name = _clean_text(interpretation.get("field_name"))
        reason_code = _clean_text(interpretation.get("reason_code"))
        if field_name is None or reason_code is None:
            continue
        if field and field_name != field:
            continue
        key = (
            project.market if project is not None else None,
            source_row.slug if source_row is not None else None,
            source_row.name if source_row is not None else None,
            field_name,
            reason_code,
        )
        total[key] += 1
        signal_flags = _mapping_or_empty(interpretation.get("signal_flags"))
        if signal_flags.get("glossary_gap_observed") is True:
            gaps[key] += 1
        if reason_code.endswith("_unmappable"):
            unmappable[key] += 1
    metrics = [
        ActivitySemanticMetricResponse(
            market=metric_market,
            source_slug=source_slug,
            source_name=source_name,
            field_name=field_name,
            field_label=_field_label(field_name),
            reason_code=reason_code,
            total_count=count,
            glossary_gap_count=gaps[key],
            unmappable_count=unmappable[key],
            glossary_gap_rate=gaps[key] / count if count else 0.0,
            unmappable_rate=unmappable[key] / count if count else 0.0,
        )
        for key, count in total.items()
        for metric_market, source_slug, source_name, field_name, reason_code in [key]
    ]
    metrics.sort(
        key=lambda item: (
            item.market or "",
            item.source_slug or "",
            item.field_name,
            item.reason_code,
        )
    )
    return ActivitySemanticMetricsResponse(
        generated_at=datetime.now(UTC).isoformat(),
        thresholds={
            "glossary_gap_rate": SEMANTIC_GAP_RATE_THRESHOLD,
            "unmappable_rate": SEMANTIC_UNMAPPABLE_RATE_THRESHOLD,
            "reviewer_rejection_sigma": SEMANTIC_REJECTION_SIGMA_THRESHOLD,
        },
        metrics=metrics,
    )


def _change_events(
    session: Session,
    *,
    source: str | None,
    field: str | None,
    actor: str | None,
    project_id: uuid.UUID | None,
    from_date: date | None,
    to_date: date | None,
    limit: int,
) -> list[ActivityEventResponse]:
    statement = select(ChangeLog).order_by(ChangeLog.timestamp.desc(), ChangeLog.id.asc())
    if source:
        statement = statement.where(ChangeLog.source == source)
    if field:
        statement = statement.where(ChangeLog.field == field)
    if actor:
        actor_conditions = [
            ChangeLog.reviewed_by_email == actor,
            ChangeLog.reviewed_by == actor,
        ]
        actor_uuid = _uuid_or_none(actor)
        if actor_uuid is not None:
            actor_conditions.append(ChangeLog.reviewed_by_user_id == actor_uuid)
        statement = statement.where(or_(*actor_conditions))
    if project_id is not None:
        statement = statement.where(ChangeLog.project_id == project_id)
    statement = _date_window(statement, ChangeLog.timestamp, from_date=from_date, to_date=to_date)
    rows = session.execute(statement.limit(limit)).scalars().all()
    projects = _projects_by_id(session, [row.project_id for row in rows])
    return [
        ActivityEventResponse(
            id=f"change:{row.id}",
            event_type="change",
            occurred_at=row.timestamp.isoformat(),
            project=_project_summary(projects.get(row.project_id)),
            source=row.source,
            source_label=_source_label(row.source),
            field=row.field,
            field_label=_field_label(row.field),
            actor_label=_actor_label(
                row.reviewed_by_email,
                row.reviewed_by,
                row.reviewed_by_user_id,
            ),
            title=f"{_field_label(row.field)} changed",
            summary=f"{_format_value(row.old_value)} to {_format_value(row.new_value)}",
            old_value=row.old_value,
            new_value=row.new_value,
            change_type=row.change_type.value,
            priority=row.priority.value,
            review_item_id=row.review_item_id,
            detail={
                "reviewed_by": row.reviewed_by,
                "reviewed_by_user_id": str(row.reviewed_by_user_id)
                if row.reviewed_by_user_id
                else None,
                "reviewed_by_email": row.reviewed_by_email,
            },
        )
        for row in rows
    ]


def _resolution_events(
    session: Session,
    *,
    source: str | None,
    field: str | None,
    project_id: uuid.UUID | None,
    from_date: date | None,
    to_date: date | None,
    limit: int,
) -> list[ActivityEventResponse]:
    if source and source != "resolution_engine":
        return []
    statement = (
        select(ResolutionLog)
        .order_by(
            ResolutionLog.created_at.desc(),
            ResolutionLog.id.asc(),
        )
        .where(ResolutionLog.current_value.is_distinct_from(ResolutionLog.resolved_value))
    )
    if field:
        statement = statement.where(ResolutionLog.field == field)
    if project_id is not None:
        statement = statement.where(ResolutionLog.project_id == project_id)
    statement = _date_window(
        statement,
        ResolutionLog.created_at,
        from_date=from_date,
        to_date=to_date,
    )
    rows = session.execute(statement.limit(limit)).scalars().all()
    projects = _projects_by_id(session, [row.project_id for row in rows])
    return [
        ActivityEventResponse(
            id=f"resolution:{row.id}",
            event_type="resolution",
            occurred_at=row.created_at.isoformat(),
            project=_project_summary(projects.get(row.project_id)),
            source="resolution_engine",
            source_label="Resolution engine",
            field=row.field,
            field_label=_field_label(row.field),
            actor_label="system",
            title=f"{_field_label(row.field)} resolved",
            summary=f"{_format_value(row.current_value)} to {_format_value(row.resolved_value)}",
            old_value=row.current_value,
            new_value=row.resolved_value,
            change_type="resolved",
            priority=None,
            detail={
                "rule_applied": row.rule_applied,
                "confidence": row.confidence.value if row.confidence else None,
                "evidence_ids": [str(evidence_id) for evidence_id in (row.evidence_ids or [])],
            },
        )
        for row in rows
    ]


def _agent_events(
    session: Session,
    *,
    source: str | None,
    actor: str | None,
    project_id: uuid.UUID | None,
    from_date: date | None,
    to_date: date | None,
    limit: int,
) -> list[ActivityEventResponse]:
    news_article_join = and_(
        AgentRun.intake_source_type == "news_article",
        AgentRun.intake_record_id == cast(NewsArticle.id, String),
    )
    statement = (
        select(AgentRun)
        .outerjoin(NewsArticle, news_article_join)
        .outerjoin(NewsSource, NewsArticle.news_source_id == NewsSource.id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.asc())
    )
    if source:
        statement = statement.where(
            or_(AgentRun.intake_source_type == source, NewsSource.slug == source)
        )
    if actor:
        statement = statement.where((AgentRun.profile_name == actor) | (AgentRun.outcome == actor))
    if project_id is not None:
        statement = statement.where(AgentRun.project_id == project_id)
    statement = _date_window(statement, AgentRun.created_at, from_date=from_date, to_date=to_date)
    rows = session.execute(statement.limit(limit)).scalars().all()
    projects = _projects_by_id(session, [row.project_id for row in rows if row.project_id])
    review_item_ids_by_agent = _review_item_ids_by_agent(session, [row.id for row in rows])
    articles = _news_articles_by_id(session, [_news_article_id(row) for row in rows])
    source_names = _news_sources_by_id(
        session,
        [article.news_source_id for article in articles.values()],
    )
    events: list[ActivityEventResponse] = []
    for row in rows:
        article_id = _news_article_id(row)
        article = articles.get(article_id) if article_id is not None else None
        news_source = source_names.get(article.news_source_id) if article is not None else None
        events.append(
            _agent_event(
                row,
                project=projects.get(row.project_id) if row.project_id else None,
                review_item_ids=review_item_ids_by_agent.get(row.id, []),
                article=article,
                news_source=news_source,
            )
        )
    return events[:limit]


def _agent_event(
    row: AgentRun,
    *,
    project: Project | None,
    review_item_ids: list[uuid.UUID],
    article: NewsArticle | None,
    news_source: NewsSource | None,
) -> ActivityEventResponse:
    trigger_text = ", ".join(row.triggered_by)
    if row.outcome in AGENT_FAILURE_OUTCOMES:
        title = AGENT_FAILURE_DISPLAY.get(
            row.outcome,
            f"Agent failed: {_source_label(row.outcome)}",
        )
    elif trigger_text:
        title = f"Agent decision: {trigger_text}"
    else:
        title = "Agent decision"
    article_summary = (
        ActivityArticleSummary(
            id=article.id,
            title=article.title,
            url=article.url_canonical,
            source_slug=news_source.slug if news_source else None,
            source_name=news_source.name if news_source else None,
            fetched_at=article.fetched_at.isoformat() if article.fetched_at else None,
            published_at=article.published_at.isoformat() if article.published_at else None,
        )
        if article
        else None
    )
    return ActivityEventResponse(
        id=f"agent:{row.id}",
        event_type="agent",
        occurred_at=row.created_at.isoformat(),
        project=_project_summary(project),
        source=row.intake_source_type,
        source_label=_source_label(row.intake_source_type),
        actor_label=row.profile_name,
        title=title,
        summary=f"{_source_label(row.outcome)} after {row.tool_calls_count} tool calls",
        review_item_ids=review_item_ids,
        article=article_summary,
        article_fetched_at=article_summary.fetched_at if article_summary else None,
        agent_created_at=row.created_at.isoformat(),
        agent_outcome=row.outcome,
        agent_triggers=list(row.triggered_by),
        agent_reasoning_trace=row.reasoning_trace,
        cost_usd=_decimal_to_float(row.cost_usd),
        detail={
            "profile_name": row.profile_name,
            "profile_version": row.profile_version,
            "provider": row.provider,
            "model": row.model,
            "prompt_version": row.prompt_version,
            "latency_ms": row.latency_ms,
            "wallclock_seconds": row.wallclock_seconds,
            "error_text": row.error_text,
            "agent_revised_verdict": row.agent_revised_verdict,
        },
    )


def _semantic_events(
    session: Session,
    *,
    source: str | None,
    field: str | None,
    project_id: uuid.UUID | None,
    from_date: date | None,
    to_date: date | None,
    limit: int,
) -> list[ActivityEventResponse]:
    rows = _semantic_rows(
        session,
        source=source,
        from_date=from_date,
        to_date=to_date,
        limit=limit if project_id is None and field is None else MAX_INTERNAL_LIMIT,
    )
    articles = _news_articles_by_id(session, [row.article_id for row in rows])
    source_names = _news_sources_by_id(
        session,
        [
            article.news_source_id
            for article in articles.values()
            if article.news_source_id is not None
        ],
    )
    references_by_extraction = _semantic_references_by_extraction(
        session,
        [row.extraction_id for row in rows],
    )
    event_specs: list[tuple[NewsSemanticInterpretation, int, dict[str, Any], uuid.UUID | None]] = []
    for row in rows:
        references = references_by_extraction.get(row.extraction_id, [])
        for index, interpretation in enumerate(_semantic_payloads(row)):
            field_name = _clean_text(interpretation.get("field_name"))
            if field_name is None:
                continue
            if field and field_name != field:
                continue
            resolved_project_id = _semantic_project_id_for_interpretation(
                interpretation,
                references,
            )
            if project_id is not None and resolved_project_id != project_id:
                continue
            event_specs.append((row, index, interpretation, resolved_project_id))
    projects = _projects_by_id(session, [project_id for _, _, _, project_id in event_specs])
    events: list[ActivityEventResponse] = []
    for row, index, interpretation, resolved_project_id in event_specs:
        article = articles.get(row.article_id)
        news_source = (
            source_names.get(article.news_source_id)
            if article is not None and article.news_source_id is not None
            else None
        )
        events.append(
            _semantic_event(
                row,
                index=index,
                interpretation=interpretation,
                project=projects.get(resolved_project_id)
                if resolved_project_id is not None
                else None,
                article=article,
                news_source=news_source,
            )
        )
    return events[:limit]


def _semantic_event(
    row: NewsSemanticInterpretation,
    *,
    index: int,
    interpretation: dict[str, Any],
    project: Project | None,
    article: NewsArticle | None,
    news_source: NewsSource | None,
) -> ActivityEventResponse:
    field_name = _clean_text(interpretation.get("field_name")) or "semantic"
    reason_code = _clean_text(interpretation.get("reason_code")) or "unknown"
    confidence = _clean_text(interpretation.get("confidence"))
    canonical_value = interpretation.get("canonical_value")
    signal_flags = _mapping_or_empty(interpretation.get("signal_flags"))
    metadata = _mapping_or_empty(interpretation.get("metadata"))
    article_summary = (
        ActivityArticleSummary(
            id=article.id,
            title=article.title,
            url=article.url_canonical,
            source_slug=news_source.slug if news_source else None,
            source_name=news_source.name if news_source else None,
            fetched_at=article.fetched_at.isoformat() if article.fetched_at else None,
            published_at=article.published_at.isoformat() if article.published_at else None,
        )
        if article
        else None
    )
    summary_parts = [reason_code]
    if confidence:
        summary_parts.append(confidence)
    if canonical_value is not None:
        summary_parts.append(_format_value(canonical_value))
    return ActivityEventResponse(
        id=f"semantic:{row.id}:{index}",
        event_type="semantic",
        occurred_at=row.created_at.isoformat(),
        project=_project_summary(project),
        source=SEMANTIC_LOGICAL_SOURCE,
        source_label=SEMANTIC_SOURCE_LABEL,
        field=field_name,
        field_label=_field_label(field_name),
        actor_label=row.prompt_id,
        title=f"{_field_label(field_name)} interpreted",
        summary=" | ".join(summary_parts),
        new_value=canonical_value,
        change_type="semantic_interpretation",
        article=article_summary,
        article_fetched_at=article_summary.fetched_at if article_summary else None,
        cost_usd=_decimal_to_float(row.cost_usd),
        detail={
            "semantic_interpretation_id": str(row.id),
            "prompt_id": row.prompt_id,
            "prompt_version": row.prompt_version,
            "prompt_hash": row.prompt_hash,
            "model": row.model,
            "model_provider": row.model_provider,
            "parse_status": row.parse_status,
            "latency_ms": row.latency_ms,
            "reason_code": reason_code,
            "confidence": confidence,
            "requires_corroboration": interpretation.get("requires_corroboration"),
            "signal_flags": signal_flags,
            "source_anchors": interpretation.get("source_anchors") or [],
            "metadata": metadata,
            "news_source_slug": news_source.slug if news_source else None,
        },
    )


def _semantic_rows(
    session: Session,
    *,
    source: str | None,
    from_date: date | None,
    to_date: date | None,
    limit: int,
) -> list[NewsSemanticInterpretation]:
    statement = (
        select(NewsSemanticInterpretation)
        .join(NewsArticle, NewsSemanticInterpretation.article_id == NewsArticle.id)
        .outerjoin(NewsSource, NewsArticle.news_source_id == NewsSource.id)
        .where(NewsSemanticInterpretation.parse_status == NewsExtractionParseStatus.OK.value)
        .order_by(NewsSemanticInterpretation.created_at.desc(), NewsSemanticInterpretation.id.asc())
    )
    if source and source not in {SEMANTIC_LOGICAL_SOURCE, "semantic"}:
        statement = statement.where(NewsSource.slug == source)
    statement = _date_window(
        statement,
        NewsSemanticInterpretation.created_at,
        from_date=from_date,
        to_date=to_date,
    )
    return list(session.execute(statement.limit(limit)).scalars().all())


def _semantic_payloads(row: NewsSemanticInterpretation) -> list[dict[str, Any]]:
    output_json = row.output_json if isinstance(row.output_json, dict) else {}
    interpretations = output_json.get("interpretations")
    if not isinstance(interpretations, list):
        return []
    return [item for item in interpretations if isinstance(item, dict)]


def _semantic_references_by_extraction(
    session: Session,
    extraction_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[NewsProjectReference]]:
    ids = sorted(set(extraction_ids))
    if not ids:
        return {}
    rows = (
        session.execute(
            select(NewsProjectReference)
            .where(NewsProjectReference.extraction_id.in_(ids))
            .order_by(NewsProjectReference.reference_index.asc())
        )
        .scalars()
        .all()
    )
    by_extraction: dict[uuid.UUID, list[NewsProjectReference]] = {}
    for row in rows:
        by_extraction.setdefault(row.extraction_id, []).append(row)
    return by_extraction


def _semantic_project_id_for_interpretation(
    interpretation: dict[str, Any],
    references: list[NewsProjectReference],
) -> uuid.UUID | None:
    if not references:
        return None
    metadata = _mapping_or_empty(interpretation.get("metadata"))
    signal_flags = _mapping_or_empty(interpretation.get("signal_flags"))
    reference_id = _first_clean_text(
        metadata.get("reference_id"),
        metadata.get("source_reference_id"),
        metadata.get("pass2b_reference_id"),
        signal_flags.get("reference_id"),
        signal_flags.get("source_reference_id"),
        signal_flags.get("pass2b_reference_id"),
    )
    reference_index = _first_int(
        metadata.get("reference_index"),
        signal_flags.get("reference_index"),
    )
    if reference_id is not None:
        parsed_id = _uuid_or_none(reference_id)
        if parsed_id is not None:
            for reference in references:
                if reference.id == parsed_id:
                    return reference.matched_project_id
    if reference_index is not None:
        for reference in references:
            if reference.reference_index == reference_index:
                return reference.matched_project_id
    if len(references) == 1:
        return references[0].matched_project_id
    return None


def _date_window(
    statement: Any,
    column: Any,
    *,
    from_date: date | None,
    to_date: date | None,
) -> Any:
    if from_date is not None:
        statement = statement.where(column >= datetime.combine(from_date, time.min, tzinfo=UTC))
    if to_date is not None:
        statement = statement.where(column <= datetime.combine(to_date, time.max, tzinfo=UTC))
    return statement


def _event_sort_key(event: ActivityEventResponse) -> datetime:
    value = event.occurred_at.replace("Z", "+00:00")
    occurred_at = datetime.fromisoformat(value)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    return occurred_at.astimezone(UTC)


def _event_matches_view(event: ActivityEventResponse, view: str) -> bool:
    if view == "all":
        return True
    if view == "agent":
        return event.event_type == "agent"
    if view == "auto_applied":
        if event.event_type == "resolution":
            return True
        if event.event_type == "change" and event.review_item_id is None:
            return True
        if event.event_type == "agent" and not event.review_item_ids:
            return True
        return False
    if view == "semantic":
        return event.event_type == "semantic"
    return True


def _projects_by_id(
    session: Session,
    project_ids: list[uuid.UUID | None],
) -> dict[uuid.UUID, Project]:
    ids = sorted({project_id for project_id in project_ids if project_id})
    if not ids:
        return {}
    rows = session.execute(select(Project).where(Project.id.in_(ids))).scalars().all()
    return {row.id: row for row in rows}


def _review_item_ids_by_agent(
    session: Session,
    agent_run_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[uuid.UUID]]:
    if not agent_run_ids:
        return {}
    links = (
        session.execute(
            select(AgentRunReviewItem).where(AgentRunReviewItem.agent_run_id.in_(agent_run_ids))
        )
        .scalars()
        .all()
    )
    by_agent: dict[uuid.UUID, list[uuid.UUID]] = {}
    for link in links:
        by_agent.setdefault(link.agent_run_id, []).append(link.review_item_id)
    return by_agent


def _news_articles_by_id(
    session: Session,
    article_ids: list[uuid.UUID | None],
) -> dict[uuid.UUID, NewsArticle]:
    ids = sorted({article_id for article_id in article_ids if article_id})
    if not ids:
        return {}
    rows = session.execute(select(NewsArticle).where(NewsArticle.id.in_(ids))).scalars().all()
    return {row.id: row for row in rows}


def _news_sources_by_id(
    session: Session,
    source_ids: list[uuid.UUID | None],
) -> dict[uuid.UUID, NewsSource]:
    ids = sorted({source_id for source_id in source_ids if source_id is not None})
    if not ids:
        return {}
    rows = session.execute(select(NewsSource).where(NewsSource.id.in_(ids))).scalars().all()
    return {row.id: row for row in rows}


def _news_article_id(row: AgentRun) -> uuid.UUID | None:
    if row.intake_source_type != "news_article":
        return None
    return _uuid_or_none(row.intake_record_id)


def _uuid_or_none(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _project_summary(project: Project | None) -> ActivityProjectSummary | None:
    if project is None:
        return None
    status = (
        project.pipeline_status.value
        if hasattr(project.pipeline_status, "value")
        else str(project.pipeline_status)
    )
    return ActivityProjectSummary(
        id=project.id,
        project_name=project.project_name,
        canonical_address=project.canonical_address,
        city=project.city,
        state=project.state,
        zip=project.zip,
        pipeline_status=status,
    )


def _field_label(value: str) -> str:
    labels = {
        "pipeline_status": "Status",
        "total_units": "Total units",
        "affordable_units": "Affordable units",
        "market_rate_units": "Market-rate units",
        "workforce_units": "Workforce units",
        "date_delivery": "Delivery date",
        "developer": "Developer",
    }
    return labels.get(value, _source_label(value))


def _source_label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _actor_label(
    email: str | None,
    legacy_actor: str | None,
    user_id: uuid.UUID | None,
) -> str:
    return email or legacy_actor or (str(user_id) if user_id else "system")


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_clean_text(*values: Any) -> str | None:
    for value in values:
        text = _clean_text(value)
        if text is not None:
            return text
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
