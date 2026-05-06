from __future__ import annotations

import hashlib
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
from tcg_pipeline.news.collectors import (
    ADVANCED_FETCH_PATH,
    POLITE_FETCH_PATH,
    SUPPORTED_FETCH_PATHS,
    AdvancedFetchRequiredError,
    DiscoveredArticleUrl,
    PoliteFetchError,
    PoliteNewsCollector,
)
from tcg_pipeline.news.extraction import (
    NewsExtractionRunResult,
    run_news_extraction_for_article,
)
from tcg_pipeline.news.ingest import ArticleFetchResult, fetch_article_pass0
from tcg_pipeline.news.integration import (
    NewsIntegrationResult,
    run_news_integration_for_article,
)
from tcg_pipeline.news.structural import apply_structural_signals
from tcg_pipeline.news.triage import NewsTriageRunResult, run_news_triage_for_article
from tcg_pipeline.news.urls import canonicalize_news_url
from tcg_pipeline.settings import Settings, get_settings
from tcg_pipeline.workers.heartbeat import write_worker_heartbeat

LOGGER = logging.getLogger(__name__)
BLOCK_LIKE_HTTP_STATUSES = frozenset({401, 403, 429, 503})
TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 504})
DEFAULT_BLOCK_LIKE_AUTO_PAUSE_THRESHOLD = 3
DEFAULT_TRANSIENT_RETRY_ATTEMPTS = 3
DEFAULT_TRANSIENT_RETRY_BACKOFF_SECONDS = 1.0
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
    market_id: uuid.UUID | None
    jurisdiction_id: uuid.UUID | None
    initiated_by_user_id: uuid.UUID | None
    initiated_by_email: str | None
    force_project_id: uuid.UUID | None
    fetch_path: str
    source_strategy_doc: str | None


@dataclass(frozen=True, slots=True)
class NewsPasteLinkIngestResult:
    job_id: uuid.UUID
    article_id: uuid.UUID
    source_run_id: uuid.UUID | None
    fetched: bool
    fetch_status: str | None
    http_status: int | None
    body_text_chars: int
    fetch_path: str | None


@dataclass(frozen=True, slots=True)
class NewsScrapePlan:
    job_id: uuid.UUID
    source_run_id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    source_name_display: str
    base_url: str
    collector_class: str
    source_config: dict
    market_slug: str
    market_id: uuid.UUID | None
    jurisdiction_id: uuid.UUID | None
    trigger_type: str
    scheduled_for: datetime | None
    incremental_since: datetime | None
    fetch_path: str
    source_strategy_doc: str | None


@dataclass(slots=True)
class NewsScrapeRunStats:
    discovered_count: int = 0
    new_article_count: int = 0
    existing_article_count: int = 0
    fetched_count: int = 0
    failed_fetch_count: int = 0
    block_like_failure_count: int = 0
    transient_failure_count: int = 0
    cost_cap_skipped_count: int = 0
    triage_relevant_count: int = 0
    extraction_ok_count: int = 0
    integration_review_item_count: int = 0
    errors: list[str] | None = None

    def add_error(self, message: str) -> None:
        if self.errors is None:
            self.errors = []
        self.errors.append(message)

    @property
    def error_text(self) -> str | None:
        if not self.errors:
            return None
        return "; ".join(self.errors)


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
    run_news_scrape_job(uuid.UUID(scrape_job_id))


def run_news_reextract_task(scrape_job_id: str) -> None:
    _run_unimplemented_news_job(uuid.UUID(scrape_job_id), ScrapeJobKind.NEWS_REEXTRACT.value)


def run_news_backfill_chunk_task(scrape_job_id: str) -> None:
    run_news_backfill_chunk_job(uuid.UUID(scrape_job_id))


def run_news_backfill_chunk_job(
    job_id: uuid.UUID,
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> None:
    resolved_session_factory = session_factory or get_session_factory()
    now = datetime.now(UTC)
    try:
        with resolved_session_factory() as session:
            job = _load_news_job(
                session,
                job_id=job_id,
                expected_kind=ScrapeJobKind.NEWS_BACKFILL_CHUNK.value,
            )
            if job is None:
                return
            payload = job.target_payload or {}
            source_slug = payload.get("source_slug")
            if source_slug is not None and not isinstance(source_slug, str):
                raise RuntimeError("News chunk backfill payload has invalid 'source_slug'.")
            article_id = _payload_uuid(payload, "article_id", required=False)
            limit = _payload_int(payload, "limit", required=False)
            apply = _payload_bool(payload, "apply", default=True)
            job.status = ScrapeJobStatus.RUNNING
            job.started_at = now
            job.progress = {
                "message": "News chunk backfill started.",
                "source_slug": source_slug,
                "article_id": str(article_id) if article_id is not None else None,
                "limit": limit,
                "apply": apply,
            }
            session.commit()
    except Exception as exc:
        _record_news_backfill_chunk_failure(
            resolved_session_factory,
            job_id=job_id,
            error=exc,
        )
        raise

    try:
        from tcg_pipeline.news.embeddings import run_news_article_chunk_indexing

        result = run_news_article_chunk_indexing(
            session_factory=resolved_session_factory,
            settings=settings,
            source_slug=source_slug,
            article_id=article_id,
            limit=limit,
            apply=apply,
            now=now,
        )
    except Exception as exc:
        _record_news_backfill_chunk_failure(
            resolved_session_factory,
            job_id=job_id,
            error=exc,
        )
        raise

    with resolved_session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        if job is None or job.status != ScrapeJobStatus.RUNNING:
            return
        job.status = ScrapeJobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        job.error_text = None
        job.progress = {
            "message": "News chunk backfill completed.",
            "apply": result.apply,
            "gated_reference_count": result.gated_reference_count,
            "planned_chunk_count": result.planned_chunk_count,
            "planned_reference_chunk_count": result.planned_reference_chunk_count,
            "planned_whole_article_chunk_count": result.planned_whole_article_chunk_count,
            "indexed_chunk_count": result.indexed_chunk_count,
            "skipped_unchanged_chunk_count": result.skipped_unchanged_chunk_count,
            "superseded_chunk_count": result.superseded_chunk_count,
            "embedding_call_count": result.embedding_call_count,
            "input_tokens": result.input_tokens,
            "cost_usd": str(result.cost_usd),
            "skipped_reason": result.skipped_reason,
        }
        session.commit()


def _record_news_backfill_chunk_failure(
    session_factory: sessionmaker[Session],
    *,
    job_id: uuid.UUID,
    error: Exception,
) -> None:
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        if job is None or job.status in {ScrapeJobStatus.COMPLETED, ScrapeJobStatus.CANCELLED}:
            return
        job.status = ScrapeJobStatus.FAILED
        job.completed_at = datetime.now(UTC)
        job.error_text = str(error)
        job.progress = {
            "message": "News chunk backfill failed.",
            "error": str(error),
        }
        raise_system_alert(
            session,
            alert_key="news_job_failed",
            severity="warning",
            message="News job failed.",
            scope={"job_id": str(job_id), "kind": ScrapeJobKind.NEWS_BACKFILL_CHUNK.value},
            detail={"error": str(error)},
        )
        session.commit()


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
            jitter_seconds = _schedule_jitter_seconds(
                source,
                settings=resolved_settings,
            )
            scheduled_due_at, applied_jitter_seconds = _scheduled_due_time(
                source_name=source.slug,
                scheduled_for=scheduled_for,
                max_jitter_seconds=jitter_seconds,
            )
            if current_time < scheduled_due_at:
                continue
            job = _create_news_scrape_job(
                session,
                source=source,
                scheduled_for=scheduled_for,
                scheduled_due_at=scheduled_due_at,
                jitter_seconds=applied_jitter_seconds,
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
                    "scheduled_for": scheduled_for.isoformat(),
                    "scheduled_due_at": scheduled_due_at.isoformat(),
                    "jitter_seconds": applied_jitter_seconds,
                }
            else:
                job.progress = {
                    "message": "Scheduled news scrape row created; Redis queue unavailable.",
                    "queue_backend": "unavailable",
                    "scheduled_for": scheduled_for.isoformat(),
                    "scheduled_due_at": scheduled_due_at.isoformat(),
                    "jitter_seconds": applied_jitter_seconds,
                }
            enqueued_count += 1
        session.commit()
    return enqueued_count


def run_news_scrape_job(
    job_id: uuid.UUID,
    *,
    collector_factory: Callable[[NewsSource], PoliteNewsCollector] = PoliteNewsCollector,
    triage_runner: Callable[[uuid.UUID], NewsTriageRunResult] | None = (
        run_news_triage_for_article
    ),
    extraction_runner: Callable[[uuid.UUID], NewsExtractionRunResult] | None = (
        run_news_extraction_for_article
    ),
    integration_runner: Callable[..., NewsIntegrationResult] | None = (
        run_news_integration_for_article
    ),
) -> None:
    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            plan = start_news_scrape_job(session, job_id=job_id)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
        _record_news_job_failure(job_id, exc)
        raise

    if plan is None:
        return

    if plan.fetch_path == ADVANCED_FETCH_PATH:
        error = _advanced_fetch_required_error_for_source(plan)
        stats = NewsScrapeRunStats()
        stats.add_error(str(error))
        _fail_news_scrape_job(session_factory, plan=plan, stats=stats, error=error)
        raise error

    stats = NewsScrapeRunStats()
    collector = collector_factory(_source_snapshot_from_plan(plan))
    try:
        discovered_urls = _discover_incremental_urls_with_retries(
            collector,
            plan=plan,
        )
        stats.discovered_count = len(discovered_urls)
        _update_news_scrape_progress(
            job_id,
            message="Discovered news article URLs.",
            progress={
                "discovered_count": stats.discovered_count,
                "fetch_path": plan.fetch_path,
            },
        )
        for discovered in discovered_urls:
            article_id, created = _persist_discovered_news_article(
                session_factory,
                plan=plan,
                discovered=discovered,
            )
            if created:
                stats.new_article_count += 1
            else:
                stats.existing_article_count += 1
                continue
            fetch_result = _fetch_article_with_retries(
                collector,
                plan=plan,
                url=discovered.url,
            )
            if fetch_result.http_status in BLOCK_LIKE_HTTP_STATUSES:
                stats.block_like_failure_count += 1
                stats.add_error(
                    f"block_like_fetch_failure: article {discovered.url} returned "
                    f"HTTP {fetch_result.http_status}"
                )
            if fetch_result.http_status in TRANSIENT_HTTP_STATUSES:
                stats.transient_failure_count += 1
                stats.add_error(
                    f"transient_fetch_failure: article {discovered.url} returned "
                    f"HTTP {fetch_result.http_status}"
                )
            ingest_result = _complete_scheduled_article_fetch(
                session_factory,
                plan=plan,
                article_id=article_id,
                result=fetch_result,
            )
            if ingest_result.fetched:
                stats.fetched_count += 1
            else:
                stats.failed_fetch_count += 1
                continue

            triage_result: NewsTriageRunResult | None = None
            if triage_runner is not None:
                triage_result = triage_runner(article_id)
                if triage_result.skipped_reason == "cost_cap":
                    stats.cost_cap_skipped_count += 1
                if triage_result.relevant:
                    stats.triage_relevant_count += 1
            extraction_result: NewsExtractionRunResult | None = None
            if (
                triage_result is not None
                and triage_result.relevant is True
                and extraction_runner is not None
            ):
                extraction_result = extraction_runner(article_id)
                if extraction_result.skipped_reason == "cost_cap":
                    stats.cost_cap_skipped_count += 1
                if extraction_result.extract_retry_skipped_reason == "cost_cap":
                    stats.cost_cap_skipped_count += 1
                if extraction_result.reextraction_skipped_reason == "cost_cap":
                    stats.cost_cap_skipped_count += 1
                if _extraction_has_ok_result(extraction_result):
                    stats.extraction_ok_count += 1
            if (
                extraction_result is not None
                and _extraction_has_ok_result(extraction_result)
                and integration_runner is not None
            ):
                integration_result = integration_runner(
                    article_id,
                    source_run_id=plan.source_run_id,
                    force_project_id=None,
                    session_factory=session_factory,
                )
                stats.integration_review_item_count += (
                    integration_result.review_items_created
                    + integration_result.review_items_updated
                )
        _finish_news_scrape_job(session_factory, plan=plan, stats=stats)
    except PoliteFetchError as exc:
        stats.add_error(_polite_fetch_error_text(exc))
        if exc.block_like:
            stats.block_like_failure_count += 1
        elif exc.status_code in TRANSIENT_HTTP_STATUSES:
            stats.transient_failure_count += 1
        _fail_news_scrape_job(session_factory, plan=plan, stats=stats, error=exc)
        raise
    except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
        stats.add_error(str(exc))
        _fail_news_scrape_job(session_factory, plan=plan, stats=stats, error=exc)
        raise
    finally:
        collector.close()


def run_news_paste_a_link_job(
    job_id: uuid.UUID,
    *,
    fetcher: Callable[[str], ArticleFetchResult] = fetch_article_pass0,
    triage_runner: Callable[[uuid.UUID], NewsTriageRunResult] | None = (
        run_news_triage_for_article
    ),
    extraction_runner: Callable[[uuid.UUID], NewsExtractionRunResult] | None = (
        run_news_extraction_for_article
    ),
    integration_runner: Callable[..., NewsIntegrationResult] | None = (
        run_news_integration_for_article
    ),
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

    if plan.fetch_path == ADVANCED_FETCH_PATH:
        error = _advanced_fetch_required_error(plan)
        _record_advanced_fetch_deferred(job_id, plan=plan, error=error)
        raise error

    result = fetcher(plan.url)
    ingest_result: NewsPasteLinkIngestResult
    with session_factory() as session:
        try:
            ingest_result = complete_news_paste_a_link_job(
                session,
                plan=plan,
                result=result,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
            session.rollback()
            _record_news_job_failure(job_id, exc)
            raise
    triage_result: NewsTriageRunResult | None = None
    if ingest_result.fetched and triage_runner is not None:
        try:
            triage_result = triage_runner(ingest_result.article_id)
        except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
            _record_news_job_failure(job_id, exc)
            raise
    extraction_result: NewsExtractionRunResult | None = None
    if (
        triage_result is not None
        and triage_result.relevant is True
        and extraction_runner is not None
    ):
        try:
            extraction_result = extraction_runner(ingest_result.article_id)
        except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
            extraction_result = NewsExtractionRunResult(
                article_id=ingest_result.article_id,
                extraction_id=None,
                relevance=None,
                reference_count=0,
                parse_status=None,
                skipped_reason="error",
                error_text=str(exc),
            )
    integration_result: NewsIntegrationResult | None = None
    if (
        extraction_result is not None
        and _extraction_has_ok_result(extraction_result)
        and integration_runner is not None
    ):
        try:
            integration_result = integration_runner(
                ingest_result.article_id,
                source_run_id=ingest_result.source_run_id,
                force_project_id=plan.force_project_id,
                session_factory=session_factory,
            )
        except Exception as exc:  # noqa: BLE001 - integration failures must persist job failure.
            _record_news_job_failure(job_id, exc)
            raise
    with session_factory() as session:
        try:
            finish_news_paste_a_link_job(
                session,
                ingest_result=ingest_result,
                triage_result=triage_result,
                extraction_result=extraction_result,
                integration_result=integration_result,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - worker tasks persist job failures.
            session.rollback()
            _record_news_job_failure(job_id, exc)
            raise


def start_news_scrape_job(
    session: Session,
    *,
    job_id: uuid.UUID,
) -> NewsScrapePlan | None:
    job = _load_news_job(
        session,
        job_id=job_id,
        expected_kind=ScrapeJobKind.NEWS_SCRAPE.value,
    )
    if job is None:
        return None
    payload = job.target_payload or {}
    source_id = _payload_uuid(payload, "news_source_id")
    assert source_id is not None
    source = session.get(NewsSource, source_id)
    if source is None:
        raise RuntimeError("Scheduled news scrape references a missing source.")
    fetch_path = _source_fetch_path(source)
    scheduled_for = _payload_datetime(payload, "scheduled_for", required=False)
    incremental_since = _payload_datetime(payload, "since", required=False)
    if incremental_since is None:
        incremental_since = _latest_scheduled_source_run(session, source)
    market_slug = source.market.slug if source.market else "unscoped"
    now = datetime.now(UTC)
    source_run = SourceRun(
        market=market_slug,
        jurisdiction_id=source.jurisdiction_id,
        source_name=source.slug,
        collection_mode="incremental",
        trigger_type=job.trigger_type.value,
        initiated_by_user_id=job.initiated_by_user_id,
        incremental_since=incremental_since,
    )
    session.add(source_run)
    session.flush()

    job.status = ScrapeJobStatus.RUNNING
    job.started_at = now
    job.source_run_id = source_run.id
    job.progress = {
        "message": "Discovering scheduled news articles.",
        "news_source_id": str(source.id),
        "source_name": source.slug,
        "fetch_path": fetch_path,
        "source_strategy_doc": _source_strategy_doc(source),
        "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
        "incremental_since": incremental_since.isoformat() if incremental_since else None,
    }
    session.flush()
    return NewsScrapePlan(
        job_id=job.id,
        source_run_id=source_run.id,
        source_id=source.id,
        source_name=source.slug,
        source_name_display=source.name,
        base_url=source.base_url,
        collector_class=source.collector_class,
        source_config=dict(source.config or {}),
        market_slug=market_slug,
        market_id=source.market_id,
        jurisdiction_id=source.jurisdiction_id,
        trigger_type=job.trigger_type.value,
        scheduled_for=scheduled_for,
        incremental_since=incremental_since,
        fetch_path=fetch_path,
        source_strategy_doc=_source_strategy_doc(source),
    )


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
    assert article_id is not None
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
        "source_name": source.slug,
        "fetch_path": _source_fetch_path(source),
    }
    session.flush()
    return NewsPasteLinkPlan(
        job_id=job.id,
        article_id=article.id,
        url=str(payload.get("url_canonical") or article.url_canonical),
        source_name=source.slug,
        market_slug=source.market.slug if source.market else "unscoped",
        market_id=source.market_id,
        jurisdiction_id=job.jurisdiction_id or source.jurisdiction_id,
        initiated_by_user_id=job.initiated_by_user_id,
        initiated_by_email=job.initiated_by_email,
        force_project_id=_payload_uuid(payload, "force_project_id", required=False),
        fetch_path=_source_fetch_path(source),
        source_strategy_doc=_source_strategy_doc(source),
    )


def complete_news_paste_a_link_job(
    session: Session,
    *,
    plan: NewsPasteLinkPlan,
    result: ArticleFetchResult,
) -> NewsPasteLinkIngestResult:
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
        return NewsPasteLinkIngestResult(
            job_id=job.id,
            article_id=article.id,
            source_run_id=job.source_run_id,
            fetched=article.fetch_status == NewsFetchStatus.FETCHED.value,
            fetch_status=article.fetch_status,
            http_status=article.http_status,
            body_text_chars=len(article.body_text or ""),
            fetch_path=plan.fetch_path,
        )

    _apply_article_fetch_result(session, article=article, result=result)
    now = datetime.now(UTC)
    fetched = result.fetch_status == NewsFetchStatus.FETCHED.value
    if fetched:
        apply_structural_signals(
            session,
            article=article,
            market_slug=plan.market_slug,
            market_id=plan.market_id,
            now=now,
        )
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

    job.source_run_id = source_run.id
    job.error_text = None
    job.progress = {
        "message": "Article ingest completed; triage pending.",
        "article_id": str(article.id),
        "fetch_status": article.fetch_status,
        "fetch_path": plan.fetch_path,
        "http_status": article.http_status,
        "body_text_chars": len(article.body_text or ""),
    }
    session.flush()
    return NewsPasteLinkIngestResult(
        job_id=job.id,
        article_id=article.id,
        source_run_id=source_run.id,
        fetched=fetched,
        fetch_status=article.fetch_status,
        http_status=article.http_status,
        body_text_chars=len(article.body_text or ""),
        fetch_path=plan.fetch_path,
    )


def finish_news_paste_a_link_job(
    session: Session,
    *,
    ingest_result: NewsPasteLinkIngestResult,
    triage_result: NewsTriageRunResult | None,
    extraction_result: NewsExtractionRunResult | None,
    integration_result: NewsIntegrationResult | None = None,
) -> ScrapeJob:
    job = session.get(ScrapeJob, ingest_result.job_id)
    if job is None:
        raise RuntimeError("News scrape job disappeared before completion.")
    if job.status != ScrapeJobStatus.RUNNING:
        return job
    now = datetime.now(UTC)
    job.status = ScrapeJobStatus.COMPLETED
    job.completed_at = now
    job.error_text = None
    job.progress = {
        "message": "Article ingest completed.",
        "article_id": str(ingest_result.article_id),
        "fetch_status": ingest_result.fetch_status,
        "fetch_path": ingest_result.fetch_path,
        "http_status": ingest_result.http_status,
        "body_text_chars": ingest_result.body_text_chars,
    }
    if triage_result is not None:
        job.progress["triage_status"] = triage_result.triage_status
        job.progress["triage_extraction_id"] = (
            str(triage_result.extraction_id) if triage_result.extraction_id else None
        )
        job.progress["triage_skipped_reason"] = triage_result.skipped_reason
    if extraction_result is not None:
        job.progress["extraction_id"] = (
            str(extraction_result.extraction_id)
            if extraction_result.extraction_id
            else None
        )
        job.progress["extraction_relevance"] = extraction_result.relevance
        job.progress["extraction_parse_status"] = extraction_result.parse_status
        job.progress["extraction_reference_count"] = extraction_result.reference_count
        job.progress["extraction_skipped_reason"] = extraction_result.skipped_reason
        job.progress["extraction_error_text"] = extraction_result.error_text
        job.progress["extract_retry_id"] = (
            str(extraction_result.extract_retry_id)
            if extraction_result.extract_retry_id
            else None
        )
        job.progress["extract_retry_attempt_count"] = (
            extraction_result.extract_retry_attempt_count
        )
        job.progress["extract_retry_parse_status"] = (
            extraction_result.extract_retry_parse_status
        )
        job.progress["extract_retry_reference_count"] = (
            extraction_result.extract_retry_reference_count
        )
        job.progress["extract_retry_skipped_reason"] = (
            extraction_result.extract_retry_skipped_reason
        )
        job.progress["extract_retry_error_text"] = extraction_result.extract_retry_error_text
        job.progress["reextraction_id"] = (
            str(extraction_result.reextraction_id)
            if extraction_result.reextraction_id
            else None
        )
        job.progress["reextraction_triggered_by"] = (
            extraction_result.reextraction_triggered_by
        )
        job.progress["reextraction_parse_status"] = (
            extraction_result.reextraction_parse_status
        )
        job.progress["reextraction_reference_count"] = (
            extraction_result.reextraction_reference_count
        )
        job.progress["reextraction_skipped_reason"] = (
            extraction_result.reextraction_skipped_reason
        )
        job.progress["reextraction_error_text"] = extraction_result.reextraction_error_text
    if integration_result is not None:
        job.progress.update(integration_result.progress_payload)
    if extraction_result is None and ingest_result.fetched:
        if triage_result is None:
            job.progress["triage_status"] = "skipped"
            job.progress["triage_skipped_reason"] = "disabled"
        elif triage_result.relevant is True:
            job.progress["extraction_skipped_reason"] = "disabled"
    session.flush()
    return job


def _extraction_has_ok_result(result: NewsExtractionRunResult) -> bool:
    return (
        result.parse_status == "ok"
        or result.extract_retry_parse_status == "ok"
        or result.reextraction_parse_status == "ok"
    )


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


def _payload_uuid(payload: dict, key: str, *, required: bool = True) -> uuid.UUID | None:
    value = payload.get(key)
    if not isinstance(value, str):
        if not required and value is None:
            return None
        raise RuntimeError(f"News scrape job payload is missing '{key}'.")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"News scrape job payload has invalid '{key}'.") from exc


def _payload_int(payload: dict, key: str, *, required: bool = True) -> int | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise RuntimeError(f"News scrape job payload is missing '{key}'.")
        return None
    if isinstance(value, bool):
        raise RuntimeError(f"News scrape job payload has invalid '{key}'.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"News scrape job payload has invalid '{key}'.") from exc


def _payload_bool(payload: dict, key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise RuntimeError(f"News scrape job payload has invalid '{key}'.")


def _payload_datetime(payload: dict, key: str, *, required: bool = True) -> datetime | None:
    value = payload.get(key)
    if not isinstance(value, str):
        if not required and value is None:
            return None
        raise RuntimeError(f"News scrape job payload is missing '{key}'.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"News scrape job payload has invalid '{key}'.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
        if article is not None and article.fetch_status == NewsFetchStatus.PENDING.value:
            article.fetch_status = NewsFetchStatus.FETCH_FAILED.value
            article.fetch_error_text = str(error)
    session.flush()
    return job


def _record_advanced_fetch_deferred(
    job_id: uuid.UUID,
    *,
    plan: NewsPasteLinkPlan,
    error: AdvancedFetchRequiredError,
) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        if job is None:
            return
        now = datetime.now(UTC)
        job.status = ScrapeJobStatus.FAILED
        job.completed_at = now
        job.error_text = str(error)
        job.progress = {
            "message": "Advanced fetch is not implemented.",
            "article_id": str(plan.article_id),
            "source_name": plan.source_name,
            "fetch_path": plan.fetch_path,
            "source_strategy_doc": plan.source_strategy_doc,
            "error": str(error),
        }
        article = session.get(NewsArticle, plan.article_id)
        if article is not None:
            article.fetch_status = NewsFetchStatus.FETCH_FAILED.value
            article.fetch_error_text = str(error)
        raise_system_alert(
            session,
            alert_key="news_advanced_fetch_deferred",
            severity="warning",
            message="News source requested advanced fetch before implementation.",
            scope={"source_name": plan.source_name, "fetch_path": plan.fetch_path},
            detail={
                "job_id": str(job_id),
                "article_id": str(plan.article_id),
                "source_strategy_doc": plan.source_strategy_doc,
                "source_doc_required": plan.source_strategy_doc is None,
            },
        )
        session.commit()


def _advanced_fetch_required_error(
    plan: NewsPasteLinkPlan,
) -> AdvancedFetchRequiredError:
    if plan.source_strategy_doc is None:
        return AdvancedFetchRequiredError(
            f"Source '{plan.source_name}' requests fetch_path='advanced' without "
            "config.source_strategy_doc; advanced fetching is deferred to D.late.ADV."
        )
    return AdvancedFetchRequiredError(
        f"Source '{plan.source_name}' requests fetch_path='advanced', but advanced "
        "fetching is deferred to D.late.ADV."
    )


def _advanced_fetch_required_error_for_source(
    plan: NewsScrapePlan,
) -> AdvancedFetchRequiredError:
    if plan.source_strategy_doc is None:
        return AdvancedFetchRequiredError(
            f"Source '{plan.source_name}' requests fetch_path='advanced' without "
            "config.source_strategy_doc; advanced fetching is deferred to D.late.ADV."
        )
    return AdvancedFetchRequiredError(
        f"Source '{plan.source_name}' requests fetch_path='advanced', but advanced "
        "fetching is deferred to D.late.ADV."
    )


def _source_fetch_path(source: NewsSource) -> str:
    config = source.config if isinstance(source.config, dict) else {}
    fetch_path = str(config.get("fetch_path") or POLITE_FETCH_PATH)
    if fetch_path not in SUPPORTED_FETCH_PATHS:
        raise RuntimeError(
            f"News source '{source.slug}' has unsupported fetch_path '{fetch_path}'."
        )
    return fetch_path


def _source_strategy_doc(source: NewsSource) -> str | None:
    config = source.config if isinstance(source.config, dict) else {}
    source_strategy_doc = config.get("source_strategy_doc")
    if isinstance(source_strategy_doc, str) and source_strategy_doc.strip():
        return source_strategy_doc
    return None


def _source_snapshot_from_plan(plan: NewsScrapePlan) -> NewsSource:
    return NewsSource(
        id=plan.source_id,
        slug=plan.source_name,
        name=plan.source_name_display,
        base_url=plan.base_url,
        collector_class=plan.collector_class,
        active=True,
        config=plan.source_config,
        market_id=plan.market_id,
        jurisdiction_id=plan.jurisdiction_id,
    )


def _discover_incremental_urls_with_retries(
    collector: PoliteNewsCollector,
    *,
    plan: NewsScrapePlan,
) -> list[DiscoveredArticleUrl]:
    max_attempts = _int_config(
        plan.source_config,
        "transient_retry_attempts",
        DEFAULT_TRANSIENT_RETRY_ATTEMPTS,
    )
    backoff_seconds = _float_config(
        plan.source_config,
        "transient_retry_backoff_seconds",
        DEFAULT_TRANSIENT_RETRY_BACKOFF_SECONDS,
    )
    for attempt in range(1, max_attempts + 1):
        try:
            return collector.discover_incremental_urls(since=plan.incremental_since)
        except PoliteFetchError as exc:
            if exc.status_code not in TRANSIENT_HTTP_STATUSES or attempt >= max_attempts:
                raise
            time.sleep(backoff_seconds * attempt)
    return []


def _fetch_article_with_retries(
    collector: PoliteNewsCollector,
    *,
    plan: NewsScrapePlan,
    url: str,
) -> ArticleFetchResult:
    max_attempts = _int_config(
        plan.source_config,
        "transient_retry_attempts",
        DEFAULT_TRANSIENT_RETRY_ATTEMPTS,
    )
    backoff_seconds = _float_config(
        plan.source_config,
        "transient_retry_backoff_seconds",
        DEFAULT_TRANSIENT_RETRY_BACKOFF_SECONDS,
    )
    result: ArticleFetchResult | None = None
    for attempt in range(1, max_attempts + 1):
        result = collector.fetch_article(url)
        if result.http_status not in TRANSIENT_HTTP_STATUSES or attempt >= max_attempts:
            return result
        time.sleep(backoff_seconds * attempt)
    assert result is not None
    return result


def _persist_discovered_news_article(
    session_factory: sessionmaker[Session],
    *,
    plan: NewsScrapePlan,
    discovered: DiscoveredArticleUrl,
) -> tuple[uuid.UUID, bool]:
    canonical_url = canonicalize_news_url(discovered.url, source_slug=plan.source_name)
    with session_factory() as session:
        existing_article = session.execute(
            select(NewsArticle).where(NewsArticle.url_hash == canonical_url.url_hash)
        ).scalar_one_or_none()
        if existing_article is not None:
            return existing_article.id, False
        article = NewsArticle(
            news_source_id=plan.source_id,
            url_canonical=canonical_url.canonical_url,
            url_original=canonical_url.original_url,
            url_hash=canonical_url.url_hash,
            fetch_status=NewsFetchStatus.PENDING.value,
            published_at=discovered.published_at or discovered.last_modified_at,
            ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
        )
        try:
            with session.begin_nested():
                session.add(article)
                session.flush()
        except IntegrityError:
            raced_article = session.execute(
                select(NewsArticle).where(NewsArticle.url_hash == canonical_url.url_hash)
            ).scalar_one()
            session.commit()
            return raced_article.id, False
        session.commit()
        return article.id, True


def _complete_scheduled_article_fetch(
    session_factory: sessionmaker[Session],
    *,
    plan: NewsScrapePlan,
    article_id: uuid.UUID,
    result: ArticleFetchResult,
) -> NewsPasteLinkIngestResult:
    with session_factory() as session:
        article = session.execute(
            select(NewsArticle)
            .where(NewsArticle.id == article_id)
            .with_for_update()
        ).scalar_one_or_none()
        if article is None:
            raise RuntimeError("Scheduled news article disappeared before fetch completion.")
        article.fetch_attempts += 1
        article.last_attempted_at = datetime.now(UTC)
        _apply_article_fetch_result(session, article=article, result=result)
        fetched = result.fetch_status == NewsFetchStatus.FETCHED.value
        if fetched:
            apply_structural_signals(
                session,
                article=article,
                market_slug=plan.market_slug,
                market_id=plan.market_id,
                now=datetime.now(UTC),
            )
        session.commit()
        return NewsPasteLinkIngestResult(
            job_id=plan.job_id,
            article_id=article.id,
            source_run_id=plan.source_run_id,
            fetched=fetched,
            fetch_status=article.fetch_status,
            http_status=article.http_status,
            body_text_chars=len(article.body_text or ""),
            fetch_path=plan.fetch_path,
        )


def _update_news_scrape_progress(
    job_id: uuid.UUID,
    *,
    message: str,
    progress: dict,
) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        if job is not None and job.status == ScrapeJobStatus.RUNNING:
            job.progress = {"message": message, **progress}
            session.commit()


def _finish_news_scrape_job(
    session_factory: sessionmaker[Session],
    *,
    plan: NewsScrapePlan,
    stats: NewsScrapeRunStats,
) -> ScrapeJob:
    with session_factory() as session:
        job = session.get(ScrapeJob, plan.job_id)
        source_run = session.get(SourceRun, plan.source_run_id)
        if job is None or source_run is None:
            raise RuntimeError("Scheduled news scrape audit rows disappeared before completion.")
        now = datetime.now(UTC)
        source_run.finished_at = now
        source_run.records_pulled = stats.discovered_count
        source_run.rows_inserted = stats.new_article_count
        source_run.rows_updated = stats.fetched_count
        source_run.rows_unchanged = stats.existing_article_count
        source_run.new_matches = stats.integration_review_item_count
        source_run.block_like_failure_count = stats.block_like_failure_count
        source_run.transient_failure_count = stats.transient_failure_count
        source_run.cost_cap_skipped_count = stats.cost_cap_skipped_count
        source_run.errors = stats.error_text
        if job.started_at is not None:
            source_run.duration_seconds = int((now - job.started_at).total_seconds())
        job.status = ScrapeJobStatus.COMPLETED
        job.completed_at = now
        job.error_text = None
        job.progress = _news_scrape_progress_payload(
            "Scheduled news scrape completed.",
            plan=plan,
            stats=stats,
        )
        _apply_news_source_failure_policy(session, plan=plan, stats=stats)
        session.commit()
        return job


def _fail_news_scrape_job(
    session_factory: sessionmaker[Session],
    *,
    plan: NewsScrapePlan,
    stats: NewsScrapeRunStats,
    error: Exception,
) -> None:
    with session_factory() as session:
        job = session.get(ScrapeJob, plan.job_id)
        source_run = session.get(SourceRun, plan.source_run_id)
        now = datetime.now(UTC)
        if source_run is not None:
            source_run.finished_at = now
            source_run.records_pulled = stats.discovered_count
            source_run.rows_inserted = stats.new_article_count
            source_run.rows_updated = stats.fetched_count
            source_run.rows_unchanged = stats.existing_article_count
            source_run.block_like_failure_count = stats.block_like_failure_count
            source_run.transient_failure_count = stats.transient_failure_count
            source_run.cost_cap_skipped_count = stats.cost_cap_skipped_count
            source_run.errors = stats.error_text or str(error)
            source_run.error_text = str(error)
            if job is not None and job.started_at is not None:
                source_run.duration_seconds = int((now - job.started_at).total_seconds())
        if job is not None:
            job.status = ScrapeJobStatus.FAILED
            job.completed_at = now
            job.error_text = str(error)
            job.progress = _news_scrape_progress_payload(
                "Scheduled news scrape failed.",
                plan=plan,
                stats=stats,
                error=str(error),
            )
        if isinstance(error, AdvancedFetchRequiredError):
            raise_system_alert(
                session,
                alert_key="news_advanced_fetch_deferred",
                severity="warning",
                message="News source requested advanced fetch before implementation.",
                scope={"source_name": plan.source_name, "fetch_path": plan.fetch_path},
                detail={
                    "job_id": str(plan.job_id),
                    "source_run_id": str(plan.source_run_id),
                    "source_strategy_doc": plan.source_strategy_doc,
                    "source_doc_required": plan.source_strategy_doc is None,
                },
            )
        _apply_news_source_failure_policy(session, plan=plan, stats=stats, error=error)
        session.commit()


def _news_scrape_progress_payload(
    message: str,
    *,
    plan: NewsScrapePlan,
    stats: NewsScrapeRunStats,
    error: str | None = None,
) -> dict:
    payload = {
        "message": message,
        "news_source_id": str(plan.source_id),
        "source_name": plan.source_name,
        "fetch_path": plan.fetch_path,
        "source_strategy_doc": plan.source_strategy_doc,
        "scheduled_for": plan.scheduled_for.isoformat() if plan.scheduled_for else None,
        "incremental_since": plan.incremental_since.isoformat() if plan.incremental_since else None,
        "discovered_count": stats.discovered_count,
        "new_article_count": stats.new_article_count,
        "existing_article_count": stats.existing_article_count,
        "fetched_count": stats.fetched_count,
        "failed_fetch_count": stats.failed_fetch_count,
        "block_like_failure_count": stats.block_like_failure_count,
        "transient_failure_count": stats.transient_failure_count,
        "cost_cap_skipped_count": stats.cost_cap_skipped_count,
        "triage_relevant_count": stats.triage_relevant_count,
        "extraction_ok_count": stats.extraction_ok_count,
        "integration_review_item_count": stats.integration_review_item_count,
    }
    if error is not None:
        payload["error"] = error
    return payload


def _apply_news_source_failure_policy(
    session: Session,
    *,
    plan: NewsScrapePlan,
    stats: NewsScrapeRunStats,
    error: Exception | None = None,
) -> None:
    if stats.block_like_failure_count <= 0:
        return
    threshold = _int_config(
        plan.source_config,
        "auto_pause_block_failures",
        DEFAULT_BLOCK_LIKE_AUTO_PAUSE_THRESHOLD,
    )
    consecutive = _consecutive_block_like_source_runs(session, plan.source_name)
    detail = {
        "source_name": plan.source_name,
        "job_id": str(plan.job_id),
        "source_run_id": str(plan.source_run_id),
        "block_like_failure_count": stats.block_like_failure_count,
        "consecutive_block_like_runs": consecutive,
        "auto_pause_threshold": threshold,
        "error": str(error) if error is not None else stats.error_text,
    }
    raise_system_alert(
        session,
        alert_key="news_source_block_like_failure",
        severity="warning",
        message="News source returned a block-like fetch response.",
        scope={"source_name": plan.source_name},
        detail=detail,
    )
    if consecutive < threshold:
        return
    source = session.get(NewsSource, plan.source_id)
    if source is not None:
        source.active = False
    raise_system_alert(
        session,
        alert_key="news_source_auto_paused",
        severity="high",
        message="News source auto-paused after repeated block-like fetch responses.",
        scope={"source_name": plan.source_name},
        detail=detail,
    )


def _consecutive_block_like_source_runs(session: Session, source_name: str) -> int:
    rows = (
        session.execute(
            select(SourceRun)
            .where(SourceRun.source_name == source_name)
            .order_by(SourceRun.run_timestamp.desc(), SourceRun.id.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    count = 0
    for row in rows:
        if row.block_like_failure_count > 0:
            count += 1
            continue
        break
    return count


def _polite_fetch_error_text(error: PoliteFetchError) -> str:
    prefix = "block_like_fetch_failure" if error.block_like else "fetch_failure"
    if error.status_code in TRANSIENT_HTTP_STATUSES:
        prefix = "transient_fetch_failure"
    return f"{prefix}: {error}"


def _int_config(config: dict, key: str, default: int) -> int:
    value = config.get(key)
    if isinstance(value, int) and value > 0:
        return value
    return default


def _float_config(config: dict, key: str, default: float) -> float:
    value = config.get(key)
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return default


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


def _schedule_jitter_seconds(source: NewsSource, *, settings: Settings) -> int:
    config = source.config if isinstance(source.config, dict) else {}
    configured = config.get("schedule_jitter_seconds")
    if isinstance(configured, int) and configured >= 0:
        return configured
    return settings.news_scheduler_jitter_seconds


def _scheduled_due_time(
    *,
    source_name: str,
    scheduled_for: datetime,
    max_jitter_seconds: int,
) -> tuple[datetime, int]:
    if max_jitter_seconds <= 0:
        return scheduled_for, 0
    normalized_scheduled_for = scheduled_for
    if normalized_scheduled_for.tzinfo is None:
        normalized_scheduled_for = normalized_scheduled_for.replace(tzinfo=UTC)
    seed = f"{source_name}:{normalized_scheduled_for.astimezone(UTC).isoformat()}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    jitter_seconds = int.from_bytes(digest[:8], "big") % (max_jitter_seconds + 1)
    return normalized_scheduled_for + timedelta(seconds=jitter_seconds), jitter_seconds


def _create_news_scrape_job(
    session: Session,
    *,
    source: NewsSource,
    scheduled_for: datetime,
    scheduled_due_at: datetime | None = None,
    jitter_seconds: int = 0,
) -> ScrapeJob | None:
    target_payload = {
        "news_source_id": str(source.id),
        "scheduled_for": scheduled_for.isoformat(),
    }
    if scheduled_due_at is not None:
        target_payload["scheduled_due_at"] = scheduled_due_at.isoformat()
    if jitter_seconds:
        target_payload["jitter_seconds"] = jitter_seconds
    job = ScrapeJob(
        jurisdiction_id=source.jurisdiction_id,
        kind=ScrapeJobKind.NEWS_SCRAPE.value,
        source_name=source.slug,
        trigger_type=ScrapeTriggerType.SCHEDULED,
        status=ScrapeJobStatus.QUEUED,
        target_payload=target_payload,
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
