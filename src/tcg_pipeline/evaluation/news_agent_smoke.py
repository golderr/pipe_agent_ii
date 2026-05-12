from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import String, distinct, func, or_, select
from sqlalchemy.orm import Session

from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE, AgentTrigger
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunReviewItem,
    LLMCostUsage,
    NewsArticle,
    NewsExtractionParseStatus,
    NewsSemanticInterpretation,
    NewsSource,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    SourceRun,
    SystemAlert,
)
from tcg_pipeline.news.costs import active_cost_cap

DEFAULT_NEWS_SMOKE_LOOKBACK_HOURS = 24
NEWS_SMOKE_ALERT_LIMIT = 50


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeSourceRun:
    source_run_id: uuid.UUID
    source_name: str
    collection_mode: str
    trigger_type: str
    run_timestamp: datetime
    finished_at: datetime | None
    records_pulled: int
    rows_inserted: int | None
    rows_updated: int | None
    rows_unchanged: int | None
    new_matches: int
    block_like_failure_count: int
    transient_failure_count: int
    cost_cap_skipped_count: int
    errors: str | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeRun:
    agent_run_id: uuid.UUID
    intake_record_id: str
    article_id: uuid.UUID | None
    article_title: str | None
    article_url: str | None
    article_source_slug: str | None
    article_fetched_at: datetime | None
    source_run_id: uuid.UUID | None
    scrape_job_id: uuid.UUID | None
    project_id: uuid.UUID | None
    triggered_by: tuple[str, ...]
    outcome: str
    cost_usd: Decimal
    review_item_count: int
    review_item_type_counts: dict[str, int]
    status_regression_review_item_count: int
    error_text: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeCostUsage:
    bucket: str
    cost_date: date
    capability: str
    provider: str
    model: str
    call_count: int
    input_tokens_uncached: int
    input_tokens_cache_creation: int
    input_tokens_cached: int
    output_tokens: int
    spent_usd: Decimal


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeCapabilityCost:
    capability: str
    call_count: int
    input_tokens_uncached: int
    input_tokens_cache_creation: int
    input_tokens_cached: int
    output_tokens: int
    spent_usd: Decimal


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeCostCapDay:
    cost_date: date
    daily_warn_usd: Decimal
    daily_hard_usd: Decimal
    spent_usd: Decimal
    warn_remaining_usd: Decimal
    hard_remaining_usd: Decimal


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeAlert:
    alert_id: uuid.UUID
    alert_key: str
    severity: str
    scope: dict | None
    message: str
    detail: dict | None
    raised_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeSemanticIssue:
    semantic_interpretation_id: uuid.UUID
    article_id: uuid.UUID
    extraction_id: uuid.UUID
    parse_status: str
    parse_error_text: str | None
    prompt_id: str
    prompt_version: str
    model: str
    provider: str
    output_tokens: int | None
    cost_usd: Decimal
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewsAgentSmokeReport:
    since: datetime
    until: datetime
    source_name: str | None
    source_names: tuple[str, ...]
    source_run_count: int
    agent_run_count: int
    outcome_counts: dict[str, int]
    trigger_counts: dict[str, int]
    review_item_type_counts: dict[str, int]
    status_regression_agent_run_count: int
    status_regression_review_item_count: int
    status_regression_open_count: int
    status_regression_auto_accepted_count: int
    agent_run_total_cost_usd: Decimal
    missing_review_link_count: int
    cost_usage_total_usd: Decimal
    cost_usage_by_capability: tuple[NewsAgentSmokeCapabilityCost, ...]
    cost_usage_rows: tuple[NewsAgentSmokeCostUsage, ...]
    cost_cap_days: tuple[NewsAgentSmokeCostCapDay, ...]
    semantic_parse_status_counts: dict[str, int]
    semantic_issue_count: int
    semantic_issues: tuple[NewsAgentSmokeSemanticIssue, ...]
    alert_count: int
    alert_limit: int
    alerts_truncated: bool
    source_runs: tuple[NewsAgentSmokeSourceRun, ...]
    runs: tuple[NewsAgentSmokeRun, ...]
    alerts: tuple[NewsAgentSmokeAlert, ...]


def build_news_agent_smoke_report(
    session: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    hours: int = DEFAULT_NEWS_SMOKE_LOOKBACK_HOURS,
    source_name: str | None = None,
    now: datetime | None = None,
) -> NewsAgentSmokeReport:
    window_since, window_until = _resolve_window(
        since=since,
        until=until,
        hours=hours,
        now=now,
    )
    source_names = _news_source_names(session, source_name=source_name)
    source_runs = _source_runs_for_window(
        session,
        since=window_since,
        until=window_until,
        source_name=source_name,
        source_names=source_names,
    )
    agent_runs = _agent_runs_for_window(
        session,
        since=window_since,
        until=window_until,
        source_name=source_name,
    )
    agent_run_ids = [run.id for run in agent_runs]
    review_link_counts = _review_link_counts(session, agent_run_ids=agent_run_ids)
    status_regression_status_counts = _status_regression_review_status_counts(
        session,
        agent_run_ids=agent_run_ids,
    )
    article_summaries = _article_summaries_by_id(
        session,
        article_ids=[
            article_id
            for article_id in (_uuid_or_none(run.intake_record_id) for run in agent_runs)
            if article_id is not None
        ],
    )
    runs = tuple(
        _smoke_run_for_agent_run(
            run,
            review_item_type_counts=review_link_counts.get(run.id, {}),
            article_summaries=article_summaries,
        )
        for run in agent_runs
    )
    cost_usage_rows = _cost_usage_rows_for_window(
        session,
        since=window_since,
        until=window_until,
    )
    cost_usage_by_capability = _cost_usage_by_capability(cost_usage_rows)
    cost_cap_days = _cost_cap_days_for_window(
        session,
        since=window_since,
        until=window_until,
        now=window_until,
    )
    semantic_rows = _semantic_rows_for_window(
        session,
        since=window_since,
        until=window_until,
        source_name=source_name,
    )
    semantic_parse_status_counts = Counter(row.parse_status for row in semantic_rows)
    semantic_issues = tuple(
        _semantic_issue(row)
        for row in semantic_rows
        if row.parse_status != NewsExtractionParseStatus.OK.value
    )
    alerts = _news_alerts_for_window(session, since=window_since, until=window_until)
    outcome_counts = Counter(run.outcome for run in runs)
    trigger_counts = Counter(trigger for run in runs for trigger in run.triggered_by)
    review_item_type_counts: Counter[str] = Counter()
    for run in runs:
        review_item_type_counts.update(run.review_item_type_counts)
    status_regression_trigger = AgentTrigger.STATUS_REGRESSION_CANDIDATE.value
    status_regression_review_type = ReviewItemType.STATUS_REGRESSION_REVIEW.value
    agent_run_total_cost = sum((run.cost_usd for run in runs), Decimal("0"))
    cost_usage_total = sum((row.spent_usd for row in cost_usage_rows), Decimal("0"))
    return NewsAgentSmokeReport(
        since=window_since,
        until=window_until,
        source_name=source_name,
        source_names=source_names,
        source_run_count=len(source_runs),
        agent_run_count=len(runs),
        outcome_counts=dict(sorted(outcome_counts.items())),
        trigger_counts=dict(sorted(trigger_counts.items())),
        review_item_type_counts=dict(sorted(review_item_type_counts.items())),
        status_regression_agent_run_count=sum(
            1 for run in runs if status_regression_trigger in run.triggered_by
        ),
        status_regression_review_item_count=review_item_type_counts.get(
            status_regression_review_type,
            0,
        ),
        status_regression_open_count=status_regression_status_counts.get(
            ReviewItemStatus.OPEN.value,
            0,
        ),
        status_regression_auto_accepted_count=status_regression_status_counts.get(
            ReviewItemStatus.AUTO_ACCEPTED.value,
            0,
        ),
        agent_run_total_cost_usd=agent_run_total_cost,
        missing_review_link_count=sum(1 for run in runs if run.review_item_count == 0),
        cost_usage_total_usd=cost_usage_total,
        cost_usage_by_capability=cost_usage_by_capability,
        cost_usage_rows=cost_usage_rows,
        cost_cap_days=cost_cap_days,
        semantic_parse_status_counts=dict(sorted(semantic_parse_status_counts.items())),
        semantic_issue_count=len(semantic_issues),
        semantic_issues=semantic_issues,
        alert_count=len(alerts.items),
        alert_limit=NEWS_SMOKE_ALERT_LIMIT,
        alerts_truncated=alerts.truncated,
        source_runs=source_runs,
        runs=runs,
        alerts=alerts.items,
    )


def validate_news_agent_smoke_report(
    report: NewsAgentSmokeReport,
    *,
    min_source_runs: int = 1,
    max_source_runs: int | None = None,
    min_agent_runs: int = 0,
    max_agent_runs: int | None = None,
    required_triggers: tuple[str, ...] = (),
    required_outcomes: tuple[str, ...] = (),
    allowed_outcomes: tuple[str, ...] = (),
    min_status_regression_review_items: int = 0,
    min_total_cost_usd: Decimal | None = None,
    max_total_cost_usd: Decimal | None = None,
    require_review_links: bool = False,
) -> list[str]:
    failures: list[str] = []
    if report.source_run_count < min_source_runs:
        failures.append(
            f"Expected at least {min_source_runs} news source runs; found "
            f"{report.source_run_count}."
        )
    if max_source_runs is not None and report.source_run_count > max_source_runs:
        failures.append(
            f"Expected at most {max_source_runs} news source runs; found "
            f"{report.source_run_count}."
        )
    if report.agent_run_count < min_agent_runs:
        failures.append(
            f"Expected at least {min_agent_runs} news agent runs; found "
            f"{report.agent_run_count}."
        )
    if max_agent_runs is not None and report.agent_run_count > max_agent_runs:
        failures.append(
            f"Expected at most {max_agent_runs} news agent runs; found "
            f"{report.agent_run_count}."
        )
    missing_triggers = sorted(set(required_triggers) - set(report.trigger_counts))
    if missing_triggers:
        failures.append(f"Missing required trigger(s): {', '.join(missing_triggers)}.")
    missing_outcomes = sorted(set(required_outcomes) - set(report.outcome_counts))
    if missing_outcomes:
        failures.append(f"Missing required outcome(s): {', '.join(missing_outcomes)}.")
    if allowed_outcomes:
        unexpected = sorted(set(report.outcome_counts) - set(allowed_outcomes))
        if unexpected:
            failures.append(f"Unexpected outcome(s): {', '.join(unexpected)}.")
    if report.status_regression_review_item_count < min_status_regression_review_items:
        failures.append(
            "Expected at least "
            f"{min_status_regression_review_items} linked status regression review item(s); "
            f"found {report.status_regression_review_item_count}."
        )
    if min_total_cost_usd is not None and report.cost_usage_total_usd < min_total_cost_usd:
        failures.append(
            "Expected total news bucket cost >= "
            f"${min_total_cost_usd}; found ${report.cost_usage_total_usd}."
        )
    if max_total_cost_usd is not None and report.cost_usage_total_usd > max_total_cost_usd:
        failures.append(
            "Expected total news bucket cost <= "
            f"${max_total_cost_usd}; found ${report.cost_usage_total_usd}."
        )
    if require_review_links and report.missing_review_link_count:
        failures.append(
            f"{report.missing_review_link_count} news agent run(s) have no linked review item."
        )
    return failures


def news_agent_smoke_report_to_dict(report: NewsAgentSmokeReport) -> dict[str, Any]:
    return {
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "source_name": report.source_name,
        "source_names": list(report.source_names),
        "source_run_count": report.source_run_count,
        "agent_run_count": report.agent_run_count,
        "outcome_counts": report.outcome_counts,
        "trigger_counts": report.trigger_counts,
        "review_item_type_counts": report.review_item_type_counts,
        "status_regression_agent_run_count": report.status_regression_agent_run_count,
        "status_regression_review_item_count": report.status_regression_review_item_count,
        "status_regression_open_count": report.status_regression_open_count,
        "status_regression_auto_accepted_count": (
            report.status_regression_auto_accepted_count
        ),
        "agent_run_total_cost_usd": str(report.agent_run_total_cost_usd),
        "missing_review_link_count": report.missing_review_link_count,
        "cost_usage_total_usd": str(report.cost_usage_total_usd),
        "cost_usage_by_capability": [
            {
                "capability": row.capability,
                "call_count": row.call_count,
                "input_tokens_uncached": row.input_tokens_uncached,
                "input_tokens_cache_creation": row.input_tokens_cache_creation,
                "input_tokens_cached": row.input_tokens_cached,
                "output_tokens": row.output_tokens,
                "spent_usd": str(row.spent_usd),
            }
            for row in report.cost_usage_by_capability
        ],
        "cost_usage_rows": [
            {
                "bucket": row.bucket,
                "cost_date": row.cost_date.isoformat(),
                "capability": row.capability,
                "provider": row.provider,
                "model": row.model,
                "call_count": row.call_count,
                "input_tokens_uncached": row.input_tokens_uncached,
                "input_tokens_cache_creation": row.input_tokens_cache_creation,
                "input_tokens_cached": row.input_tokens_cached,
                "output_tokens": row.output_tokens,
                "spent_usd": str(row.spent_usd),
            }
            for row in report.cost_usage_rows
        ],
        "cost_cap_days": [
            {
                "cost_date": row.cost_date.isoformat(),
                "daily_warn_usd": str(row.daily_warn_usd),
                "daily_hard_usd": str(row.daily_hard_usd),
                "spent_usd": str(row.spent_usd),
                "warn_remaining_usd": str(row.warn_remaining_usd),
                "hard_remaining_usd": str(row.hard_remaining_usd),
            }
            for row in report.cost_cap_days
        ],
        "semantic_parse_status_counts": report.semantic_parse_status_counts,
        "semantic_issue_count": report.semantic_issue_count,
        "semantic_issues": [
            {
                "semantic_interpretation_id": str(issue.semantic_interpretation_id),
                "article_id": str(issue.article_id),
                "extraction_id": str(issue.extraction_id),
                "parse_status": issue.parse_status,
                "parse_error_text": issue.parse_error_text,
                "prompt_id": issue.prompt_id,
                "prompt_version": issue.prompt_version,
                "model": issue.model,
                "provider": issue.provider,
                "output_tokens": issue.output_tokens,
                "cost_usd": str(issue.cost_usd),
                "created_at": issue.created_at.isoformat(),
            }
            for issue in report.semantic_issues
        ],
        "alert_count": report.alert_count,
        "alert_limit": report.alert_limit,
        "alerts_truncated": report.alerts_truncated,
        "source_runs": [
            {
                "source_run_id": str(source_run.source_run_id),
                "source_name": source_run.source_name,
                "collection_mode": source_run.collection_mode,
                "trigger_type": source_run.trigger_type,
                "run_timestamp": source_run.run_timestamp.isoformat(),
                "finished_at": (
                    source_run.finished_at.isoformat() if source_run.finished_at else None
                ),
                "records_pulled": source_run.records_pulled,
                "rows_inserted": source_run.rows_inserted,
                "rows_updated": source_run.rows_updated,
                "rows_unchanged": source_run.rows_unchanged,
                "new_matches": source_run.new_matches,
                "block_like_failure_count": source_run.block_like_failure_count,
                "transient_failure_count": source_run.transient_failure_count,
                "cost_cap_skipped_count": source_run.cost_cap_skipped_count,
                "errors": source_run.errors,
                "error_text": source_run.error_text,
            }
            for source_run in report.source_runs
        ],
        "runs": [
            {
                "agent_run_id": str(run.agent_run_id),
                "intake_record_id": run.intake_record_id,
                "article_id": str(run.article_id) if run.article_id else None,
                "article_title": run.article_title,
                "article_url": run.article_url,
                "article_source_slug": run.article_source_slug,
                "article_fetched_at": (
                    run.article_fetched_at.isoformat() if run.article_fetched_at else None
                ),
                "source_run_id": str(run.source_run_id) if run.source_run_id else None,
                "scrape_job_id": str(run.scrape_job_id) if run.scrape_job_id else None,
                "project_id": str(run.project_id) if run.project_id else None,
                "triggered_by": list(run.triggered_by),
                "outcome": run.outcome,
                "cost_usd": str(run.cost_usd),
                "review_item_count": run.review_item_count,
                "review_item_type_counts": run.review_item_type_counts,
                "status_regression_review_item_count": (
                    run.status_regression_review_item_count
                ),
                "error_text": run.error_text,
                "created_at": run.created_at.isoformat(),
            }
            for run in report.runs
        ],
        "alerts": [
            {
                "alert_id": str(alert.alert_id),
                "alert_key": alert.alert_key,
                "severity": alert.severity,
                "scope": alert.scope,
                "message": alert.message,
                "detail": alert.detail,
                "raised_at": alert.raised_at.isoformat(),
                "last_seen_at": alert.last_seen_at.isoformat(),
                "cleared_at": alert.cleared_at.isoformat() if alert.cleared_at else None,
            }
            for alert in report.alerts
        ],
    }


def _resolve_window(
    *,
    since: datetime | None,
    until: datetime | None,
    hours: int,
    now: datetime | None,
) -> tuple[datetime, datetime]:
    if hours <= 0:
        raise ValueError("hours must be positive.")
    resolved_until = _utc_datetime(until or now or datetime.now(UTC))
    resolved_since = (
        _utc_datetime(since) if since is not None else resolved_until - timedelta(hours=hours)
    )
    if resolved_since > resolved_until:
        raise ValueError("since must be before until.")
    return resolved_since, resolved_until


def _news_source_names(session: Session, *, source_name: str | None) -> tuple[str, ...]:
    if source_name is not None:
        return (source_name,)
    rows = session.execute(select(NewsSource.slug).order_by(NewsSource.slug.asc())).scalars().all()
    return tuple(rows)


def _source_runs_for_window(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    source_name: str | None,
    source_names: tuple[str, ...],
) -> tuple[NewsAgentSmokeSourceRun, ...]:
    if source_name is None and not source_names:
        return ()
    statement = (
        select(SourceRun)
        .where(
            SourceRun.run_timestamp >= since,
            SourceRun.run_timestamp <= until,
        )
        .order_by(SourceRun.run_timestamp.asc(), SourceRun.id.asc())
    )
    if source_name is not None:
        statement = statement.where(SourceRun.source_name == source_name)
    else:
        statement = statement.where(SourceRun.source_name.in_(source_names))
    return tuple(
        NewsAgentSmokeSourceRun(
            source_run_id=row.id,
            source_name=row.source_name,
            collection_mode=row.collection_mode,
            trigger_type=row.trigger_type,
            run_timestamp=row.run_timestamp,
            finished_at=row.finished_at,
            records_pulled=row.records_pulled,
            rows_inserted=row.rows_inserted,
            rows_updated=row.rows_updated,
            rows_unchanged=row.rows_unchanged,
            new_matches=row.new_matches,
            block_like_failure_count=row.block_like_failure_count,
            transient_failure_count=row.transient_failure_count,
            cost_cap_skipped_count=row.cost_cap_skipped_count,
            errors=row.errors,
            error_text=row.error_text,
        )
        for row in session.execute(statement).scalars().all()
    )


def _agent_runs_for_window(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    source_name: str | None,
) -> list[AgentRun]:
    statement = (
        select(AgentRun)
        .where(
            AgentRun.profile_name == NEWS_AGENT_PROFILE.name,
            AgentRun.created_at >= since,
            AgentRun.created_at <= until,
        )
        .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
    )
    if source_name is not None:
        statement = statement.join(SourceRun, AgentRun.source_run_id == SourceRun.id).where(
            SourceRun.source_name == source_name
        )
    return list(session.execute(statement).scalars().all())


def _smoke_run_for_agent_run(
    run: AgentRun,
    *,
    review_item_type_counts: dict[str, int],
    article_summaries: dict[uuid.UUID, dict[str, Any]],
) -> NewsAgentSmokeRun:
    # News profile contract: intake_record_id is the stringified news_articles.id.
    article_id = _uuid_or_none(run.intake_record_id)
    article = article_summaries.get(article_id) if article_id is not None else None
    sorted_review_item_type_counts = dict(sorted(review_item_type_counts.items()))
    return NewsAgentSmokeRun(
        agent_run_id=run.id,
        intake_record_id=run.intake_record_id,
        article_id=article_id,
        article_title=article["title"] if article else None,
        article_url=article["url_canonical"] if article else None,
        article_source_slug=article["source_slug"] if article else None,
        article_fetched_at=article["fetched_at"] if article else None,
        source_run_id=run.source_run_id,
        scrape_job_id=run.scrape_job_id,
        project_id=run.project_id,
        triggered_by=tuple(str(trigger) for trigger in run.triggered_by),
        outcome=str(run.outcome),
        cost_usd=_decimal(run.cost_usd),
        review_item_count=sum(sorted_review_item_type_counts.values()),
        review_item_type_counts=sorted_review_item_type_counts,
        status_regression_review_item_count=sorted_review_item_type_counts.get(
            ReviewItemType.STATUS_REGRESSION_REVIEW.value,
            0,
        ),
        error_text=run.error_text,
        created_at=run.created_at,
    )


def _article_summaries_by_id(
    session: Session,
    *,
    article_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, Any]]:
    if not article_ids:
        return {}
    rows = session.execute(
        select(
            NewsArticle.id,
            NewsArticle.title,
            NewsArticle.url_canonical,
            NewsArticle.fetched_at,
            NewsSource.slug,
        )
        .join(NewsSource, NewsArticle.news_source_id == NewsSource.id)
        .where(NewsArticle.id.in_(article_ids))
    ).all()
    return {
        row.id: {
            "title": row.title,
            "url_canonical": row.url_canonical,
            "fetched_at": row.fetched_at,
            "source_slug": row.slug,
        }
        for row in rows
    }


def _cost_usage_rows_for_window(
    session: Session,
    *,
    since: datetime,
    until: datetime,
) -> tuple[NewsAgentSmokeCostUsage, ...]:
    rows = session.execute(
        select(LLMCostUsage)
        .where(
            LLMCostUsage.bucket == NEWS_AGENT_PROFILE.cost_cap_bucket,
            LLMCostUsage.cost_date >= since.date(),
            LLMCostUsage.cost_date <= until.date(),
        )
        .order_by(
            LLMCostUsage.cost_date.asc(),
            LLMCostUsage.capability.asc(),
            LLMCostUsage.provider.asc(),
            LLMCostUsage.model.asc(),
        )
    ).scalars().all()
    return tuple(
        NewsAgentSmokeCostUsage(
            bucket=row.bucket,
            cost_date=row.cost_date,
            capability=row.capability,
            provider=row.provider,
            model=row.model,
            call_count=row.call_count,
            input_tokens_uncached=row.input_tokens_uncached,
            input_tokens_cache_creation=row.input_tokens_cache_creation,
            input_tokens_cached=row.input_tokens_cached,
            output_tokens=row.output_tokens,
            spent_usd=_decimal(row.spent_usd),
        )
        for row in rows
    )


def _cost_usage_by_capability(
    rows: tuple[NewsAgentSmokeCostUsage, ...],
) -> tuple[NewsAgentSmokeCapabilityCost, ...]:
    totals: dict[str, dict[str, int | Decimal]] = {}
    for row in rows:
        item = totals.setdefault(
            row.capability,
            {
                "call_count": 0,
                "input_tokens_uncached": 0,
                "input_tokens_cache_creation": 0,
                "input_tokens_cached": 0,
                "output_tokens": 0,
                "spent_usd": Decimal("0"),
            },
        )
        item["call_count"] += row.call_count
        item["input_tokens_uncached"] += row.input_tokens_uncached
        item["input_tokens_cache_creation"] += row.input_tokens_cache_creation
        item["input_tokens_cached"] += row.input_tokens_cached
        item["output_tokens"] += row.output_tokens
        item["spent_usd"] += row.spent_usd
    return tuple(
        NewsAgentSmokeCapabilityCost(
            capability=capability,
            call_count=int(item["call_count"]),
            input_tokens_uncached=int(item["input_tokens_uncached"]),
            input_tokens_cache_creation=int(item["input_tokens_cache_creation"]),
            input_tokens_cached=int(item["input_tokens_cached"]),
            output_tokens=int(item["output_tokens"]),
            spent_usd=Decimal(item["spent_usd"]),
        )
        for capability, item in sorted(totals.items())
    )


def _cost_cap_days_for_window(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    now: datetime,
) -> tuple[NewsAgentSmokeCostCapDay, ...]:
    days: list[NewsAgentSmokeCostCapDay] = []
    current = since.date()
    final = until.date()
    while current <= final:
        cap = active_cost_cap(
            session,
            cost_date=current,
            bucket=NEWS_AGENT_PROFILE.cost_cap_bucket,
            now=now,
        )
        warn_remaining = cap.daily_warn_usd - cap.spent_usd
        hard_remaining = cap.daily_hard_usd - cap.spent_usd
        days.append(
            NewsAgentSmokeCostCapDay(
                cost_date=current,
                daily_warn_usd=cap.daily_warn_usd,
                daily_hard_usd=cap.daily_hard_usd,
                spent_usd=cap.spent_usd,
                warn_remaining_usd=max(warn_remaining, Decimal("0")),
                hard_remaining_usd=max(hard_remaining, Decimal("0")),
            )
        )
        current = current + timedelta(days=1)
    return tuple(days)


def _semantic_rows_for_window(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    source_name: str | None,
) -> tuple[NewsSemanticInterpretation, ...]:
    statement = (
        select(NewsSemanticInterpretation)
        .where(
            NewsSemanticInterpretation.created_at >= since,
            NewsSemanticInterpretation.created_at <= until,
        )
        .order_by(
            NewsSemanticInterpretation.created_at.asc(),
            NewsSemanticInterpretation.id.asc(),
        )
    )
    if source_name is not None:
        statement = (
            statement.join(NewsArticle, NewsSemanticInterpretation.article_id == NewsArticle.id)
            .join(NewsSource, NewsArticle.news_source_id == NewsSource.id)
            .where(NewsSource.slug == source_name)
        )
    return tuple(session.execute(statement).scalars().all())


def _semantic_issue(row: NewsSemanticInterpretation) -> NewsAgentSmokeSemanticIssue:
    return NewsAgentSmokeSemanticIssue(
        semantic_interpretation_id=row.id,
        article_id=row.article_id,
        extraction_id=row.extraction_id,
        parse_status=row.parse_status,
        parse_error_text=row.parse_error_text,
        prompt_id=row.prompt_id,
        prompt_version=row.prompt_version,
        model=row.model,
        provider=row.model_provider,
        output_tokens=row.output_tokens,
        cost_usd=_decimal(row.cost_usd),
        created_at=row.created_at,
    )


@dataclass(frozen=True, slots=True)
class _AlertWindow:
    items: tuple[NewsAgentSmokeAlert, ...]
    truncated: bool


def _news_alerts_for_window(
    session: Session,
    *,
    since: datetime,
    until: datetime,
) -> _AlertWindow:
    rows = session.execute(
        select(SystemAlert)
        .where(
            _news_alert_clause(),
            or_(
                SystemAlert.cleared_at.is_(None),
                SystemAlert.raised_at >= since,
                SystemAlert.last_seen_at >= since,
            ),
            SystemAlert.raised_at <= until,
        )
        .order_by(
            SystemAlert.last_seen_at.desc(),
            SystemAlert.raised_at.desc(),
            SystemAlert.id.asc(),
        )
        .limit(NEWS_SMOKE_ALERT_LIMIT + 1)
    ).scalars().all()
    truncated = len(rows) > NEWS_SMOKE_ALERT_LIMIT
    visible_rows = rows[:NEWS_SMOKE_ALERT_LIMIT]
    return _AlertWindow(
        items=tuple(
            NewsAgentSmokeAlert(
                alert_id=row.id,
                alert_key=row.alert_key,
                severity=row.severity,
                scope=row.scope,
                message=row.message,
                detail=row.detail,
                raised_at=row.raised_at,
                last_seen_at=row.last_seen_at,
                cleared_at=row.cleared_at,
            )
            for row in visible_rows
        ),
        truncated=truncated,
    )


def _news_alert_clause():
    return or_(
        SystemAlert.alert_key.like("news_%"),
        SystemAlert.alert_key.like("agent_news_v1_%"),
        SystemAlert.scope["bucket"].astext == NEWS_AGENT_PROFILE.cost_cap_bucket,
        SystemAlert.scope["profile_name"].astext == NEWS_AGENT_PROFILE.name,
        SystemAlert.scope["component"].astext.like("news%"),
    )


def _review_link_counts(
    session: Session,
    *,
    agent_run_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    if not agent_run_ids:
        return {}
    rows = session.execute(
        select(
            AgentRunReviewItem.agent_run_id,
            ReviewItem.item_type,
            func.count(ReviewItem.id),
        )
        .join(ReviewItem, AgentRunReviewItem.review_item_id == ReviewItem.id)
        .where(AgentRunReviewItem.agent_run_id.in_(agent_run_ids))
        .group_by(AgentRunReviewItem.agent_run_id, ReviewItem.item_type)
    ).all()
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for row in rows:
        item_type = getattr(row.item_type, "value", str(row.item_type))
        counts.setdefault(row.agent_run_id, {})[item_type] = int(row[2])
    return counts


def _status_regression_review_status_counts(
    session: Session,
    *,
    agent_run_ids: list[uuid.UUID],
) -> dict[str, int]:
    if not agent_run_ids:
        return {}
    rows = session.execute(
        select(
            ReviewItem.status,
            func.count(distinct(ReviewItem.id)),
        )
        .join(AgentRunReviewItem, AgentRunReviewItem.review_item_id == ReviewItem.id)
        .where(
            AgentRunReviewItem.agent_run_id.in_(agent_run_ids),
            ReviewItem.item_type.cast(String) == ReviewItemType.STATUS_REGRESSION_REVIEW.value,
        )
        .group_by(ReviewItem.status)
    ).all()
    return {getattr(row.status, "value", str(row.status)): int(row[1]) for row in rows}


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or "0"))
