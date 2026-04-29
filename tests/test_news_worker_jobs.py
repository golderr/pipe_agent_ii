from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    Jurisdiction,
    Market,
    NewsSource,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    SystemAlert,
)
from tcg_pipeline.settings import Settings
from tcg_pipeline.workers import news_jobs, scrape_jobs
from tcg_pipeline.workers.heartbeat import (
    worker_heartbeat_is_fresh,
    write_worker_heartbeat,
)


def test_enqueue_news_job_execution_queues_expected_task(monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list[dict[str, object]] = []

    class FakeQueue:
        def enqueue(self, path: str, job_id: str, **kwargs: object) -> None:
            queued.append({"path": path, "job_id": job_id, **kwargs})

    monkeypatch.setattr(scrape_jobs, "scrape_job_queue", lambda **_kwargs: FakeQueue())
    job_id = uuid.uuid4()
    settings = Settings(
        app_env="test",
        redis_url="redis://example.test:6379/0",
        scrape_job_timeout_seconds=10,
        scrape_job_result_ttl_seconds=20,
        scrape_job_failure_ttl_seconds=30,
    )

    assert news_jobs.enqueue_news_job_execution(
        job_id,
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        settings=settings,
    )
    assert queued == [
        {
            "path": "tcg_pipeline.workers.news_jobs.run_news_paste_a_link_task",
            "job_id": str(job_id),
            "job_timeout": 10,
            "result_ttl": 20,
            "failure_ttl": 30,
        }
    ]


def test_enqueue_news_job_execution_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported news scrape job kind"):
        news_jobs.enqueue_news_job_execution(
            uuid.uuid4(),
            kind="news_unknown",
            settings=Settings(app_env="test", redis_url=None),
        )


def test_scheduled_fire_time_respects_last_run_and_catchup() -> None:
    pytest.importorskip("croniter")
    now = datetime(2026, 4, 28, 13, 1, tzinfo=UTC)

    scheduled_for = news_jobs._scheduled_fire_time(
        schedule_cron="0 13 * * *",
        schedule_timezone="UTC",
        last_run_at=None,
        now=now,
        catchup_hours=24,
    )
    assert scheduled_for == datetime(2026, 4, 28, 13, 0, tzinfo=UTC)

    already_ran = news_jobs._scheduled_fire_time(
        schedule_cron="0 13 * * *",
        schedule_timezone="UTC",
        last_run_at=datetime(2026, 4, 28, 13, 0, tzinfo=UTC),
        now=now,
        catchup_hours=24,
    )
    assert already_ran is None


def test_heartbeat_write_and_freshness(postgres_session: Session) -> None:
    _ensure_worker_tables(postgres_session)
    now = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    write_worker_heartbeat(
        postgres_session,
        worker_name="test-worker",
        metadata={"queue_name": "scrape_jobs"},
        now=now,
    )
    postgres_session.flush()

    assert worker_heartbeat_is_fresh(
        postgres_session,
        worker_name="test-worker",
        max_age_seconds=300,
        now=now + timedelta(seconds=299),
    )
    assert not worker_heartbeat_is_fresh(
        postgres_session,
        worker_name="test-worker",
        max_age_seconds=300,
        now=now + timedelta(seconds=301),
    )


def test_unimplemented_news_task_marks_job_failed_and_alerts(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_worker_tables(postgres_session)
    job = ScrapeJob(
        jurisdiction_id=None,
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        source_name="news_paste_a_link",
        target_payload={"url": "https://example.com/article"},
        status=ScrapeJobStatus.QUEUED,
    )
    postgres_session.add(job)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    monkeypatch.setattr(news_jobs, "get_session_factory", lambda: task_session_factory)

    with pytest.raises(NotImplementedError, match="not implemented"):
        news_jobs.run_news_paste_a_link_task(str(job.id))

    postgres_session.expire_all()
    refreshed_job = postgres_session.get(ScrapeJob, job.id)
    assert refreshed_job is not None
    assert refreshed_job.status == ScrapeJobStatus.FAILED
    assert "not implemented" in (refreshed_job.error_text or "")
    alert = postgres_session.execute(
        select(SystemAlert).where(SystemAlert.alert_key == "news_job_failed")
    ).scalar_one()
    assert alert.scope == {
        "job_id": str(job.id),
        "kind": ScrapeJobKind.NEWS_PASTE_A_LINK.value,
    }


def test_duplicate_scheduled_news_job_keeps_session_usable(
    postgres_session: Session,
) -> None:
    _ensure_news_scheduler_tables(postgres_session)
    unique_id = uuid.uuid4().hex
    market = Market(
        slug=f"test-market-{unique_id}",
        name="Test Market",
        state="CA",
    )
    postgres_session.add(market)
    postgres_session.flush()
    jurisdiction = Jurisdiction(
        slug=f"test-jurisdiction-{unique_id}",
        name="Test Jurisdiction",
        state="CA",
        market_id=market.id,
    )
    source = NewsSource(
        slug=f"test-news-source-{unique_id}",
        name="Test News Source",
        base_url="https://example.com",
        collector_class="TestCollector",
        market_id=market.id,
        jurisdiction_id=jurisdiction.id,
    )
    postgres_session.add_all([jurisdiction, source])
    postgres_session.flush()
    existing_job = ScrapeJob(
        jurisdiction_id=jurisdiction.id,
        kind=ScrapeJobKind.NEWS_SCRAPE.value,
        source_name=source.slug,
        status=ScrapeJobStatus.QUEUED,
        target_payload={"existing": True},
    )
    postgres_session.add(existing_job)
    postgres_session.flush()

    duplicate = news_jobs._create_news_scrape_job(
        postgres_session,
        source=source,
        scheduled_for=datetime(2026, 4, 28, 13, 0, tzinfo=UTC),
    )

    assert duplicate is None
    alert = SystemAlert(
        alert_key=f"duplicate-session-check-{unique_id}",
        severity="info",
        scope={"test": unique_id},
        message="Session remained usable after duplicate scheduled news job.",
    )
    postgres_session.add(alert)
    postgres_session.flush()
    postgres_session.expire_all()
    assert postgres_session.get(NewsSource, source.id) is not None
    assert postgres_session.get(ScrapeJob, existing_job.id) is not None
    assert postgres_session.get(SystemAlert, alert.id) is not None


def _ensure_worker_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {"scrape_jobs", "system_alerts", "worker_heartbeats"}
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply D.1 migrations before running worker tests: {missing}")
    scrape_job_columns = {
        column["name"] for column in inspector.get_columns("scrape_jobs")
    }
    if "kind" not in scrape_job_columns:
        pytest.skip("Apply migration 202604290020 before running worker tests.")


def _ensure_news_scheduler_tables(postgres_session: Session) -> None:
    _ensure_worker_tables(postgres_session)
    inspector = inspect(postgres_session.bind)
    required_tables = {"markets", "jurisdictions", "news_sources"}
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply D.1 migrations before running scheduler tests: {missing}")
