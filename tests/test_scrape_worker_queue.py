from __future__ import annotations

import uuid

from tcg_pipeline.settings import Settings
from tcg_pipeline.workers import scrape_jobs


def test_enqueue_scrape_job_execution_returns_false_without_redis() -> None:
    settings = Settings(app_env="test", redis_url=None)

    assert scrape_jobs.enqueue_scrape_job_execution(uuid.uuid4(), settings=settings) is False


def test_enqueue_scrape_job_execution_queues_when_redis_configured(
    monkeypatch,
) -> None:
    queued: list[dict[str, object]] = []

    class FakeQueue:
        def enqueue(self, path: str, job_id: str, **kwargs: object) -> None:
            queued.append({"path": path, "job_id": job_id, **kwargs})

    monkeypatch.setattr(scrape_jobs, "scrape_job_queue", lambda **_kwargs: FakeQueue())
    job_id = uuid.uuid4()
    settings = Settings(
        app_env="test",
        redis_url="redis://example.test:6379/0",
        scrape_job_timeout_seconds=123,
        scrape_job_result_ttl_seconds=456,
        scrape_job_failure_ttl_seconds=789,
    )

    assert scrape_jobs.enqueue_scrape_job_execution(job_id, settings=settings) is True
    assert queued == [
        {
            "path": "tcg_pipeline.workers.scrape_jobs.run_scrape_job_task",
            "job_id": str(job_id),
            "job_timeout": 123,
            "result_ttl": 456,
            "failure_ttl": 789,
        }
    ]


def test_enqueue_scrape_job_execution_falls_back_when_enqueue_fails(
    monkeypatch,
) -> None:
    class FakeQueue:
        def enqueue(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(scrape_jobs, "scrape_job_queue", lambda **_kwargs: FakeQueue())
    settings = Settings(app_env="test", redis_url="redis://example.test:6379/0")

    assert scrape_jobs.enqueue_scrape_job_execution(uuid.uuid4(), settings=settings) is False


def test_scrape_queue_status_reports_unconfigured() -> None:
    status = scrape_jobs.scrape_queue_status(settings=Settings(app_env="test", redis_url=None))

    assert status.configured is False
    assert status.available is False
    assert status.queue_name == "scrape_jobs"
    assert status.error == "REDIS_URL is not configured."
