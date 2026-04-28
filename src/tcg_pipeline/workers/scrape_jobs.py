from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from tcg_pipeline.settings import Settings, get_settings
from tcg_pipeline.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrapeQueueStatus:
    configured: bool
    available: bool
    queue_name: str
    queued_jobs: int = 0
    started_jobs: int = 0
    failed_jobs: int = 0
    worker_count: int = 0
    error: str | None = None


def enqueue_scrape_job_execution(
    job_id: uuid.UUID,
    *,
    settings: Settings | None = None,
) -> bool:
    queue = scrape_job_queue(settings=settings)
    if queue is None:
        return False
    resolved_settings = _settings(settings)
    try:
        queue.enqueue(
            "tcg_pipeline.workers.scrape_jobs.run_scrape_job_task",
            str(job_id),
            job_timeout=resolved_settings.scrape_job_timeout_seconds,
            result_ttl=resolved_settings.scrape_job_result_ttl_seconds,
            failure_ttl=resolved_settings.scrape_job_failure_ttl_seconds,
        )
    except Exception:
        LOGGER.warning(
            "Could not enqueue scrape job %s in RQ; falling back to API background task.",
            job_id,
            exc_info=True,
        )
        return False
    return True


def run_scrape_job_task(job_id: str) -> None:
    # Late import avoids a module cycle: coverage router imports the worker queue helpers.
    from tcg_pipeline.api.routers.coverage import run_scrape_job

    run_scrape_job(uuid.UUID(job_id))


def scrape_job_queue(*, settings: Settings | None = None) -> Any | None:
    resolved_settings = _settings(settings)
    redis_url = _clean(resolved_settings.redis_url)
    if redis_url is None:
        return None
    redis_cls, queue_cls, _worker_cls = _rq_imports()
    return queue_cls(
        resolved_settings.scrape_job_queue_name,
        connection=redis_cls.from_url(redis_url),
    )


def scrape_queue_status(*, settings: Settings | None = None) -> ScrapeQueueStatus:
    resolved_settings = _settings(settings)
    redis_url = _clean(resolved_settings.redis_url)
    if redis_url is None:
        return ScrapeQueueStatus(
            configured=False,
            available=False,
            queue_name=resolved_settings.scrape_job_queue_name,
            error="REDIS_URL is not configured.",
        )

    try:
        redis_cls, queue_cls, worker_cls = _rq_imports()
        connection = redis_cls.from_url(redis_url)
        connection.ping()
        queue = queue_cls(resolved_settings.scrape_job_queue_name, connection=connection)
        workers = worker_cls.all(connection=connection)
        return ScrapeQueueStatus(
            configured=True,
            available=True,
            queue_name=queue.name,
            queued_jobs=queue.count,
            started_jobs=queue.started_job_registry.count,
            failed_jobs=queue.failed_job_registry.count,
            worker_count=len(workers),
        )
    except Exception as exc:  # noqa: BLE001 - health endpoint should report status.
        return ScrapeQueueStatus(
            configured=True,
            available=False,
            queue_name=resolved_settings.scrape_job_queue_name,
            error=f"{exc.__class__.__name__}: {exc}",
        )


def run_worker(
    *,
    settings: Settings | None = None,
    queue_name: str | None = None,
    burst: bool = False,
) -> bool:
    resolved_settings = _settings(settings)
    redis_url = _clean(resolved_settings.redis_url)
    if redis_url is None:
        raise RuntimeError("REDIS_URL is required to run the scrape worker.")

    configure_logging(resolved_settings.log_level)
    redis_cls, queue_cls, worker_cls = _rq_imports()
    connection = redis_cls.from_url(redis_url)
    queue = queue_cls(queue_name or resolved_settings.scrape_job_queue_name, connection=connection)
    worker = worker_cls([queue], connection=connection)
    return worker.work(burst=burst)


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _rq_imports() -> tuple[Any, Any, Any]:
    try:
        from redis import Redis
        from rq import Queue, Worker
    except ImportError as exc:
        raise RuntimeError(
            "Redis/RQ dependencies are not installed. Run `pip install -e .` before "
            "starting the scrape worker."
        ) from exc
    return Redis, Queue, Worker
