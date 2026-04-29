from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsSource,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    ScrapeTriggerType,
    SourceRun,
    SystemAlert,
)
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
    _run_unimplemented_news_job(uuid.UUID(scrape_job_id), ScrapeJobKind.NEWS_PASTE_A_LINK.value)


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


def _run_unimplemented_news_job(job_id: uuid.UUID, expected_kind: str) -> None:
    session_factory = get_session_factory()
    now = datetime.now(UTC)
    try:
        with session_factory() as session:
            job = _load_news_job(session, job_id=job_id, expected_kind=expected_kind)
            if job is None:
                return
            job.status = ScrapeJobStatus.RUNNING
            job.started_at = now
            job.progress = {"message": f"Running {expected_kind}."}
            session.flush()
            write_worker_heartbeat(
                session,
                worker_name=f"job:{expected_kind}",
                active_job_id=job.id,
                active_job_started_at=now,
                metadata={"kind": expected_kind},
            )
            session.commit()

        raise NotImplementedError(
            f"{expected_kind} pipeline is not implemented until later Phase D."
        )
    except Exception as exc:
        with session_factory() as session:
            job = session.get(ScrapeJob, job_id)
            if job is not None:
                job.status = ScrapeJobStatus.FAILED
                job.completed_at = datetime.now(UTC)
                job.error_text = str(exc)
                job.progress = {"message": "News job failed."}
            raise_system_alert(
                session,
                alert_key="news_job_failed",
                severity="warning",
                message="News job failed.",
                scope={"job_id": str(job_id), "kind": expected_kind},
                detail={"error": str(exc)},
            )
            session.commit()
        raise


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
    alert = session.execute(
        select(SystemAlert).where(
            SystemAlert.alert_key == alert_key,
            SystemAlert.scope == normalized_scope,
            SystemAlert.cleared_at.is_(None),
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if alert is None:
        alert = SystemAlert(
            alert_key=alert_key,
            severity=severity,
            scope=normalized_scope,
            message=message,
            detail=detail,
            raised_at=now,
            last_seen_at=now,
        )
        session.add(alert)
    else:
        alert.severity = severity
        alert.message = message
        alert.detail = detail
        alert.last_seen_at = now
    session.flush()
    return alert
