from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.api.schemas import (
    ActivityArticleSummary,
    ActivityEventResponse,
    ActivityFeedResponse,
    ActivityProjectSummary,
)
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunReviewItem,
    ChangeLog,
    NewsArticle,
    NewsSource,
    Project,
    ResolutionLog,
)

router = APIRouter(prefix="/activity", tags=["activity"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)

EVENT_TYPES = {"change", "resolution", "agent"}
VIEW_PRESETS = {"all", "agent", "auto_applied", "semantic"}
AGENT_FAILURE_OUTCOMES = {"failed_timeout", "failed_budget", "failed_error", "killed_by_switch"}
MAX_INTERNAL_LIMIT = 500


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

    filtered_events = [
        event
        for event in events
        if _event_matches_view(event, normalized_view)
    ]
    filtered_events.sort(key=_event_sort_key, reverse=True)
    return ActivityFeedResponse(
        generated_at=datetime.now(UTC).isoformat(),
        events=filtered_events[:limit],
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
    statement = select(ResolutionLog).order_by(
        ResolutionLog.created_at.desc(),
        ResolutionLog.id.asc(),
    ).where(
        ResolutionLog.current_value.is_distinct_from(ResolutionLog.resolved_value)
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
    statement = select(AgentRun).order_by(AgentRun.created_at.desc(), AgentRun.id.asc())
    if actor:
        statement = statement.where((AgentRun.profile_name == actor) | (AgentRun.outcome == actor))
    if project_id is not None:
        statement = statement.where(AgentRun.project_id == project_id)
    statement = _date_window(statement, AgentRun.created_at, from_date=from_date, to_date=to_date)
    query_limit = MAX_INTERNAL_LIMIT if source else limit
    rows = session.execute(statement.limit(query_limit)).scalars().all()
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
        if not _agent_source_matches(row, article=article, news_source=news_source, source=source):
            continue
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
        title = f"Agent failed: {_source_label(row.outcome)}"
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
        if event.field != "pipeline_status":
            return False
        source_key = event.source.lower()
        return "news" in source_key or "semantic" in source_key or "urbanize" in source_key
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
    links = session.execute(
        select(AgentRunReviewItem).where(AgentRunReviewItem.agent_run_id.in_(agent_run_ids))
    ).scalars().all()
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
    source_ids: list[uuid.UUID],
) -> dict[uuid.UUID, NewsSource]:
    ids = sorted(set(source_ids))
    if not ids:
        return {}
    rows = session.execute(select(NewsSource).where(NewsSource.id.in_(ids))).scalars().all()
    return {row.id: row for row in rows}


def _agent_source_matches(
    row: AgentRun,
    *,
    article: NewsArticle | None,
    news_source: NewsSource | None,
    source: str | None,
) -> bool:
    if source is None:
        return True
    if row.intake_source_type == source:
        return True
    if article is None or news_source is None:
        return False
    return news_source.slug == source


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
