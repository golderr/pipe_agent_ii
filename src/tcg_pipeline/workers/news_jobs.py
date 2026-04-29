from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsFetchStatus,
    NewsSource,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    ScrapeTriggerType,
    SourceRun,
    SystemAlert,
)
from tcg_pipeline.news.ingest import ArticleFetchResult, fetch_article_pass0
from tcg_pipeline.settings import Settings, get_settings
from tcg_pipeline.workers.heartbeat import write_worker_heartbeat

LOGGER = logging.getLogger(__name__)
NEWS_JOB_TASK_BY_KIND = {
    ScrapeJobKind.NEWS_PASTE_A_LINK.value: (
        "tcg_pipeline.workers.news_jobs.run_news_paste_a_link_task"
    ),
    ScrapeJobKind.NEWS_SCRAPE.value: "tcg_pipeline.workers.news_jobs.run_news_scrape_task",
    ScrapeJobKind.NEWS_REEXTRACT.value: "tcg_pipeline.workers.news_jobs.run_news_reextract_task",
    ScrapeJobKind.NEWS_BACKFILL_CHUNK.value: (
        "tcg_pipeline.workers.news_jobs.run_news_backfill_chunk_task"
    ),
}
NEWS_JOB_KINDS = frozenset(NEWS_JOB_TASK_BY_KIND)


@dataclass(frozen=True, slots=True)
class NewsPasteLinkPlan:
    job_id: uuid.UUID
    article_id: uuid.UUID
    url: str
    source_name: str
    market_slug: str
    jurisdiction_id: uuid.UUID | None
    initiated_by_user_id: uuid.UUID | None
    initiated_by_email: str | None


def enqueue_news_job_execution(
    job_id: uuid.UUID,
    *,
    kind: str,
    settings: Settings | None = None,
) -> bool:
    task_path = NEWS_JOB_TASK_BY_KIND.get(kind)
    if task_path is None:
        raise ValueError(f"Unsupported news scrape job kind: {kind}")
    from tcg_pipeline.workers.scrape_jobs import scrape_job_queue

    queue = scrape_job_queue(settings=settings)
    if queue is None:
        return False
    resolved_settings = settings or get_settings()
    try:
        queue.enqueue(
            task_path,
            str(job_id),
            job_timeout=resolved_settings.scrape_job_timeout_seconds,
            result_ttl=resolved_settings.scrape_job_result_ttl_seconds,
            failure_ttl=resolved_settings.scrape_job_failure_ttl_seconds,
        )
    except Exception:
        LOGGER.warning("Could not enqueue news job %s.", job_id, exc_info=True)
        return False
    return True


def start_news_scheduler_thread(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> threading.Thread | None:
    if not settings.news_scheduler_leader:
        return None

    def loop() -> None:
        while True:
            try:
                enqueued_count = scheduler_tick(
                    session_factory=session_factory,
                    settings=settings,
                )
                with session_factory() as session:
                    write_worker_heartbeat(
                        session,
                        worker_name="scheduler",
                        metadata={"enqueued_count": enqueued_count},
                    )
                    session.commit()
            except Exception:
                LOGGER.warning("News scheduler tick failed.", exc_info=True)
            time.sleep(settings.news_scheduler_interval_seconds)

    thread = threading.Thread(target=loop, name="news-scheduler", daemon=True)
    thread.start()
    LOGGER.info("News scheduler thread started.")
    return thread


def run_news_paste_a_link_task(scrape_job_id: str) -> None:
    run_news_paste_a_link_job(uuid.UUID(scrape_job_id))


def run_news_scrape_task(scrape_job_id: str) -> None:
    _run_unimplemented_news_job(uuid.UUID(scrape_job_id), ScrapeJobKind.NEWS_SCRAPE.value)


def run_news_reextract_task(scrape_job_id: str) -> None:
    _run_unimplemented_news_job(uuid.UUID(scrape_job_id), ScrapeJobKind.NEWS_REEXTRACT.value)


def run_news_backfill_chunk_task(scrape_job_id: str) -> None:
    _run_unimplemented_news_job(uuid.UUID(scrape_job_id), ScrapeJobKind.NEWS_BACKFILL_CHUNK.value)


def scheduler_tick(
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or get_session_factory()
    current_time = now or datetime.now(UTC)
    enqueued_count = 0
    with resolved_session_factory() as session:
        sources = session.execute(
            select(NewsSource).where(
                NewsSource.active.is_(True),
                NewsSource.schedule_cron.is_not(None),
            )
        ).scalars()
        for source in sources:
            scheduled_for = _scheduled_fire_time(
                schedule_cron=source.schedule_cron,
                schedule_timezone=source.schedule_timezone,
                last_run_at=_latest_scheduled_source_run(session, source),
                now=current_time,
                catchup_hours=resolved_settings.news_scheduler_catchup_hours,
            )
            if scheduled_for is None:
                continue
            job = _create_news_scrape_job(
                session,
                source=source,
                scheduled_for=scheduled_for,
            )
            if job is None:
                continue
            if enqueue_news_job_execution(
                job.id,
                kind=ScrapeJobKind.NEWS_SCRAPE.value,
                settings=resolved_settings,
            ):
                job.progress = {
                    "message": "Queued scheduled news scrape.",
                    "queue_backend": "rq",
                }
            else:
                job.progress = {
                    "message": "Scheduled news scrape row created; Redis queue unavailable.",
                    "queue_backend": "unavailable",
                }
            enqueued_count += 1
        session.commit()
    return enqueued_count


def run_news_paste_a_link_job(
    job_id: uuid.UUID,
    *,
    fetcher: Callable[[str], ArticleFetchResult] = fetch_article_pass0,
) -> None:
    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            plan = start_news_paste_a_link_job(session, job_id=job_id)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
        _record_news_job_failure(job_id, exc)
        raise

    if plan is None:
        return

    result = fetcher(plan.url)
    with session_factory() as session:
        try:
            complete_news_paste_a_link_job(session, plan=plan, result=result)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
            session.rollback()
            _record_news_job_failure(job_id, exc)
            raise


def start_news_paste_a_link_job(
    session: Session,
    *,
    job_id: uuid.UUID,
) -> NewsPasteLinkPlan | None:
    job = _load_news_job(
        session,
        job_id=job_id,
        expected_kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
    )
    if job is None:
        return None
    payload = job.target_payload or {}
    article_id = _payload_uuid(payload, "article_id")
    article = session.get(NewsArticle, article_id)
    if article is None:
        raise RuntimeError("News paste-a-link job references a missing article.")
    source = article.source
    now = datetime.now(UTC)
    article.fetch_attempts += 1
    article.last_attempted_at = now
    job.status = ScrapeJobStatus.RUNNING
    job.started_at = now
    job.progress = {
        "message": "Fetching and parsing article.",
        "article_id": str(article.id),
    }
    session.flush()
    return NewsPasteLinkPlan(
        job_id=job.id,
        article_id=article.id,
        url=str(payload.get("url_canonical") or article.url_canonical),
        source_name=source.slug,
        market_slug=source.market.slug if source.market else "unscoped",
        jurisdiction_id=job.jurisdiction_id or source.jurisdiction_id,
        initiated_by_user_id=job.initiated_by_user_id,
        initiated_by_email=job.initiated_by_email,
    )


def complete_news_paste_a_link_job(
    session: Session,
    *,
    plan: NewsPasteLinkPlan,
    result: ArticleFetchResult,
) -> ScrapeJob:
    article = session.execute(
        select(NewsArticle)
        .where(NewsArticle.id == plan.article_id)
        .with_for_update()
    ).scalar_one_or_none()
    if article is None:
        raise RuntimeError("News article disappeared before Pass 0 completion.")
    job = session.get(ScrapeJob, plan.job_id)
    if job is None:
        raise RuntimeError("News scrape job disappeared before Pass 0 completion.")
    if job.status != ScrapeJobStatus.RUNNING:
        return job

    _apply_article_fetch_result(session, article=article, result=result)
    now = datetime.now(UTC)
    fetched = result.fetch_status == NewsFetchStatus.FETCHED.value
    source_run = SourceRun(
        market=plan.market_slug,
        jurisdiction_id=plan.jurisdiction_id,
        source_name=plan.source_name,
        collection_mode="single",
        trigger_type=ScrapeTriggerType.USER_INITIATED.value,
        initiated_by_user_id=plan.initiated_by_user_id,
        finished_at=now,
        records_pulled=1 if fetched else 0,
        rows_updated=1 if fetched else 0,
        errors=_source_run_error_text(result),
    )
    session.add(source_run)
    session.flush()

    job.status = ScrapeJobStatus.COMPLETED
    job.completed_at = now
    job.source_run_id = source_run.id
    job.error_text = None
    job.progress = {
        "message": "Article ingest completed.",
        "article_id": str(article.id),
        "fetch_status": article.fetch_status,
        "http_status": article.http_status,
        "body_text_chars": len(article.body_text or ""),
    }
    session.flush()
    return job


def _run_unimplemented_news_job(job_id: uuid.UUID, expected_kind: str) -> None:
    session_factory = get_session_factory()
    now = datetime.now(UTC)
    error = NotImplementedError(
        f"{expected_kind} pipeline is not implemented until later Phase D."
    )
    with session_factory() as session:
        job = _load_news_job(session, job_id=job_id, expected_kind=expected_kind)
        if job is None:
            return
        job.status = ScrapeJobStatus.FAILED
        job.started_at = now
        job.completed_at = now
        job.error_text = str(error)
        job.progress = {"message": "News job failed.", "error": str(error)}
        raise_system_alert(
            session,
            alert_key="news_job_failed",
            severity="warning",
            message="News job failed.",
            scope={"job_id": str(job_id), "kind": expected_kind},
            detail={"error": str(error)},
        )
        session.commit()
    raise error


def _load_news_job(
    session: Session,
    *,
    job_id: uuid.UUID,
    expected_kind: str,
) -> ScrapeJob | None:
    job = session.get(ScrapeJob, job_id)
    if job is None or job.status != ScrapeJobStatus.QUEUED:
        return None
    if job.kind != expected_kind:
        raise RuntimeError(f"Expected {expected_kind} job, found {job.kind}.")
    return job


def _payload_uuid(payload: dict, key: str) -> uuid.UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f"News scrape job payload is missing '{key}'.")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"News scrape job payload has invalid '{key}'.") from exc


def _apply_article_fetch_result(
    session: Session,
    *,
    article: NewsArticle,
    result: ArticleFetchResult,
) -> None:
    now = datetime.now(UTC)
    article.fetch_status = result.fetch_status
    article.fetched_at = now if result.fetch_status == NewsFetchStatus.FETCHED.value else None
    article.fetch_error_text = result.error_text
    article.http_status = result.http_status
    article.raw_html = result.raw_html
    article.raw_html_hash = result.raw_html_hash
    article.body_text = result.body_text
    article.body_text_hash = result.body_text_hash
    article.title = result.title
    article.byline_author = result.byline_author
    article.published_at = result.published_at
    article.publication_section = result.publication_section
    article.tags = result.tags
    article.external_article_id = result.external_article_id
    article.language = result.language or "en"
    article.paywall_state = result.paywall_state
    if result.body_text_hash:
        duplicate = session.execute(
            select(NewsArticle)
            .where(
                NewsArticle.body_text_hash == result.body_text_hash,
                NewsArticle.id != article.id,
            )
            .limit(1)
        ).scalar_one_or_none()
        if duplicate is not None:
            article.notes = _append_note(
                article.notes,
                f"Body text duplicates article {duplicate.id}.",
            )
    session.flush()


def _source_run_error_text(result: ArticleFetchResult) -> str | None:
    if result.fetch_status in {
        NewsFetchStatus.FETCH_FAILED.value,
        NewsFetchStatus.PARSE_FAILED.value,
    }:
        return result.error_text
    return None


def _append_note(existing_note: str | None, new_note: str) -> str:
    if existing_note and existing_note.strip():
        return f"{existing_note.rstrip()}\n{new_note}"
    return new_note


def _record_news_job_failure(job_id: uuid.UUID, error: Exception) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            mark_news_job_failed(session, job_id=job_id, error=error)
            session.commit()
        except Exception:
            session.rollback()


def mark_news_job_failed(
    session: Session,
    *,
    job_id: uuid.UUID,
    error: Exception,
) -> ScrapeJob | None:
    job = session.get(ScrapeJob, job_id)
    if job is None or job.status in {ScrapeJobStatus.COMPLETED, ScrapeJobStatus.CANCELLED}:
        return job
    job.status = ScrapeJobStatus.FAILED
    job.completed_at = datetime.now(UTC)
    job.error_text = str(error)
    job.progress = {"message": "News job failed."}
    if job.target_payload and isinstance(job.target_payload.get("article_id"), str):
        article = session.get(NewsArticle, uuid.UUID(job.target_payload["article_id"]))
        if article is not None:
            article.fetch_status = NewsFetchStatus.FETCH_FAILED.value
            article.fetch_error_text = str(error)
    session.flush()
    return job


def _latest_scheduled_source_run(session: Session, source: NewsSource) -> datetime | None:
    return session.execute(
        select(func.max(SourceRun.run_timestamp)).where(
            SourceRun.source_name == source.slug,
            SourceRun.trigger_type == ScrapeTriggerType.SCHEDULED.value,
        )
    ).scalar_one()


def _scheduled_fire_time(
    *,
    schedule_cron: str | None,
    schedule_timezone: str | None,
    last_run_at: datetime | None,
    now: datetime,
    catchup_hours: int,
) -> datetime | None:
    if not schedule_cron:
        return None
    try:
        timezone = ZoneInfo(schedule_timezone or "UTC")
    except ZoneInfoNotFoundError:
        LOGGER.warning("Invalid news source schedule timezone: %s", schedule_timezone)
        return None
    local_now = now.astimezone(timezone)
    from croniter import croniter

    previous_fire = croniter(schedule_cron, local_now).get_prev(datetime)
    if local_now - previous_fire > timedelta(hours=catchup_hours):
        return None
    if last_run_at is not None:
        last_run_local = last_run_at
        if last_run_local.tzinfo is None:
            last_run_local = last_run_local.replace(tzinfo=UTC)
        if last_run_local.astimezone(timezone) >= previous_fire:
            return None
    return previous_fire.astimezone(UTC)


def _create_news_scrape_job(
    session: Session,
    *,
    source: NewsSource,
    scheduled_for: datetime,
) -> ScrapeJob | None:
    job = ScrapeJob(
        jurisdiction_id=source.jurisdiction_id,
        kind=ScrapeJobKind.NEWS_SCRAPE.value,
        source_name=source.slug,
        trigger_type=ScrapeTriggerType.SCHEDULED,
        status=ScrapeJobStatus.QUEUED,
        target_payload={
            "news_source_id": str(source.id),
            "scheduled_for": scheduled_for.isoformat(),
        },
        progress={"message": "Scheduled news scrape created."},
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        return None
    return job


def raise_system_alert(
    session: Session,
    *,
    alert_key: str,
    severity: str,
    message: str,
    scope: dict | None = None,
    detail: dict | None = None,
) -> SystemAlert:
    normalized_scope = scope or {}
    now = datetime.now(UTC)
    statement = (
        insert(SystemAlert)
        .values(
            alert_key=alert_key,
            severity=severity,
            scope=normalized_scope,
            message=message,
            detail=detail,
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
                "severity": severity,
                "message": message,
                "detail": detail,
                "last_seen_at": now,
            },
        )
        .returning(SystemAlert.id)
    )
    alert_id = session.execute(statement).scalar_one()
    alert = session.get(SystemAlert, alert_id)
    if alert is None:
        raise RuntimeError("System alert upsert did not return a persisted row.")
    return alert
