"""Recovery for news articles stranded after a failed news_agent_integrate job.

A stranded article is one that completed Pass 0/1/2a/2b cleanly (triage relevant,
``current_extraction_id`` set, candidate references created) but never finished
Pass 2c, matching, agent, or integrator. Stranding typically follows a transient
provider error (Supabase SSL, Anthropic 5xx) inside ``news_agent_integrate`` -
or, before the 2026-05-08 worker-model split, inside an inline integration step
of ``news_paste_a_link``. The article is left with pending ``news_project_references``
and no ``agent_runs``; the daily smoke report has no signal for this state.

This module finds those articles and re-enqueues a ``news_agent_integrate`` job
so the existing integrator can finish the pipeline on a fresh transaction. The
companion ``news-reprocess-stranded`` CLI in ``cli.py`` is the operator entry
point.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, cast, exists, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    AgentRun,
    NewsArticle,
    NewsProjectReference,
    NewsSemanticInterpretation,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
)
from tcg_pipeline.settings import Settings

REPROCESS_TRIGGER_SENTINEL = "reprocess_stranded"


@dataclass(frozen=True, slots=True)
class StrandedArticle:
    """Snapshot of a stranded article and the context needed to re-enqueue it."""

    article_id: uuid.UUID
    title: str | None
    url_canonical: str | None
    fetched_at: datetime | None
    triage_status: str | None
    current_extraction_id: uuid.UUID | None
    pending_reference_count: int
    total_reference_count: int
    # Recovery context, sourced from the most recent failed scrape_job for the article.
    source_run_id: uuid.UUID | None
    parent_job_id: uuid.UUID | None
    trigger_reasons: tuple[str, ...]
    last_failed_job_id: uuid.UUID | None
    last_failed_job_kind: str | None
    last_failed_error_text: str | None


@dataclass(frozen=True, slots=True)
class ReprocessOutcome:
    """Result of attempting to re-enqueue one stranded article."""

    article_id: uuid.UUID
    enqueued: bool
    reused_existing_job: bool
    job_id: uuid.UUID | None
    triggers_used: tuple[str, ...]
    skipped_reason: str | None
    error_text: str | None


def find_stranded_articles(
    session: Session,
    *,
    article_ids: Iterable[uuid.UUID] | None = None,
    fetched_since: datetime | None = None,
    require_failed_job: bool = True,
) -> list[StrandedArticle]:
    """Return articles whose pipeline halted between Pass 2b and Pass 2c.

    Criteria: triage_status='relevant', current_extraction_id IS NOT NULL,
    at least one news_project_reference with match_status='pending',
    no agent_runs row keyed by the article, and no semantic interpretation row.

    When ``require_failed_job`` is True (the default) only articles whose recovery
    context resolves to a tracked failed ``scrape_jobs`` row are returned. That
    excludes pre-Pass-2c historical staging-smoke artifacts that match the
    structural criteria but were never produced by a real failed job.
    """
    has_pending_ref = exists(
        select(NewsProjectReference.id)
        .where(NewsProjectReference.article_id == NewsArticle.id)
        .where(NewsProjectReference.match_status == "pending")
    )
    has_agent_run = exists(
        select(AgentRun.id).where(
            AgentRun.intake_record_id == cast(NewsArticle.id, String)
        )
    )
    has_semantic = exists(
        select(NewsSemanticInterpretation.id).where(
            NewsSemanticInterpretation.article_id == NewsArticle.id
        )
    )
    statement = (
        select(NewsArticle)
        .where(NewsArticle.triage_status == "relevant")
        .where(NewsArticle.current_extraction_id.is_not(None))
        .where(has_pending_ref)
        .where(~has_agent_run)
        .where(~has_semantic)
        .order_by(NewsArticle.fetched_at.desc().nulls_last(), NewsArticle.id.asc())
    )
    if article_ids is not None:
        ids = list(article_ids)
        if not ids:
            return []
        statement = statement.where(NewsArticle.id.in_(ids))
    if fetched_since is not None:
        statement = statement.where(NewsArticle.fetched_at >= fetched_since)

    stranded: list[StrandedArticle] = []
    for article in session.execute(statement).scalars().all():
        ref_counts = session.execute(
            select(NewsProjectReference.match_status, NewsProjectReference.id).where(
                NewsProjectReference.article_id == article.id
            )
        ).all()
        total = len(ref_counts)
        pending = sum(1 for r in ref_counts if r[0] == "pending")
        context = _recovery_context_for_article(session, article_id=article.id)
        if require_failed_job and context["last_failed_job_id"] is None:
            continue
        stranded.append(
            StrandedArticle(
                article_id=article.id,
                title=article.title,
                url_canonical=article.url_canonical,
                fetched_at=article.fetched_at,
                triage_status=article.triage_status,
                current_extraction_id=article.current_extraction_id,
                pending_reference_count=pending,
                total_reference_count=total,
                **context,
            )
        )
    return stranded


def _recovery_context_for_article(
    session: Session,
    *,
    article_id: uuid.UUID,
) -> dict:
    """Derive source_run_id / parent_job_id / triggers from the most recent
    failed scrape_job for this article. Falls back to None where unknown."""
    job = session.execute(
        select(ScrapeJob)
        .where(ScrapeJob.target_payload["article_id"].astext == str(article_id))
        .where(ScrapeJob.status == ScrapeJobStatus.FAILED)
        .where(
            ScrapeJob.kind.in_(
                [
                    ScrapeJobKind.NEWS_AGENT_INTEGRATE.value,
                    ScrapeJobKind.NEWS_PASTE_A_LINK.value,
                ]
            )
        )
        .order_by(ScrapeJob.queued_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return {
            "source_run_id": None,
            "parent_job_id": None,
            "trigger_reasons": (REPROCESS_TRIGGER_SENTINEL,),
            "last_failed_job_id": None,
            "last_failed_job_kind": None,
            "last_failed_error_text": None,
        }
    payload = job.target_payload if isinstance(job.target_payload, dict) else {}
    triggers_raw = payload.get("trigger_reasons")
    if isinstance(triggers_raw, list) and triggers_raw:
        triggers = tuple(str(t) for t in triggers_raw)
    else:
        triggers = (REPROCESS_TRIGGER_SENTINEL,)
    parent_payload_raw = payload.get("parent_job_id")
    parent_job_id: uuid.UUID | None
    if isinstance(parent_payload_raw, str):
        try:
            parent_job_id = uuid.UUID(parent_payload_raw)
        except ValueError:
            parent_job_id = job.id
    else:
        # Pre-2026-05-08 inline integration: use the failed paste-a-link job id
        # as the parent so the audit trail still points at something real.
        parent_job_id = job.id
    return {
        "source_run_id": job.source_run_id,
        "parent_job_id": parent_job_id,
        "trigger_reasons": triggers,
        "last_failed_job_id": job.id,
        "last_failed_job_kind": job.kind,
        "last_failed_error_text": job.error_text,
    }


def reprocess_stranded_article(
    *,
    session_factory: sessionmaker[Session],
    article: StrandedArticle,
    settings: Settings,
) -> ReprocessOutcome:
    """Enqueue a fresh news_agent_integrate job for one stranded article.

    Returns a ReprocessOutcome describing whether a job was created/reused and
    whether the Redis enqueue succeeded. The DB row is created even when Redis
    is unavailable; the existing worker loader picks up the row on next sweep.
    """
    # Local import to avoid a worker -> reprocess module import cycle at startup.
    from tcg_pipeline.workers.news_jobs import _enqueue_news_agent_integrate_job

    if article.source_run_id is None:
        return ReprocessOutcome(
            article_id=article.article_id,
            enqueued=False,
            reused_existing_job=False,
            job_id=None,
            triggers_used=article.trigger_reasons,
            skipped_reason="no_source_run_id",
            error_text=(
                "Cannot reprocess: no source_run_id available from prior scrape_jobs "
                "for this article. Manual recovery required."
            ),
        )

    parent_job_id = article.parent_job_id or article.article_id
    # Look up the article's news_source to get source_name + jurisdiction context
    # the existing enqueue helper expects.
    with session_factory() as session:
        article_row = session.get(NewsArticle, article.article_id)
        if article_row is None:
            return ReprocessOutcome(
                article_id=article.article_id,
                enqueued=False,
                reused_existing_job=False,
                job_id=None,
                triggers_used=article.trigger_reasons,
                skipped_reason="article_missing",
                error_text=None,
            )
        source = article_row.source
        source_name = source.slug if source is not None else "unknown"
        jurisdiction_id = None  # news_articles are not jurisdiction-scoped today.

    try:
        ok = _enqueue_news_agent_integrate_job(
            session_factory,
            article_id=article.article_id,
            source_run_id=article.source_run_id,
            parent_job_id=parent_job_id,
            source_name=source_name,
            jurisdiction_id=jurisdiction_id,
            trigger_reasons=article.trigger_reasons,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - operator tool must report, not crash.
        return ReprocessOutcome(
            article_id=article.article_id,
            enqueued=False,
            reused_existing_job=False,
            job_id=None,
            triggers_used=article.trigger_reasons,
            skipped_reason="enqueue_error",
            error_text=str(exc),
        )

    # Re-read the created/reused job to surface its id and progress state.
    with session_factory() as session:
        job = session.execute(
            select(ScrapeJob)
            .where(ScrapeJob.kind == ScrapeJobKind.NEWS_AGENT_INTEGRATE.value)
            .where(ScrapeJob.target_payload["article_id"].astext == str(article.article_id))
            .order_by(ScrapeJob.queued_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        job_id = job.id if job is not None else None
        # If the helper found an active job and reused it, the progress will say "Reused".
        reused = bool(
            job is not None
            and isinstance(job.progress, dict)
            and job.progress.get("deduplicated") is True
        )

    return ReprocessOutcome(
        article_id=article.article_id,
        enqueued=ok,
        reused_existing_job=reused,
        job_id=job_id,
        triggers_used=article.trigger_reasons,
        skipped_reason=None,
        error_text=None
        if ok
        else "News-agent integration row was created, but Redis enqueue failed.",
    )


def fetched_since_window(*, days: int, now: datetime | None = None) -> datetime:
    """Convenience: build the fetched_since cutoff from a day count."""
    reference = now if now is not None else datetime.now(UTC)
    return reference - timedelta(days=days)
