from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import (
    Jurisdiction,
    Market,
    NewsArticle,
    NewsFetchStatus,
    NewsSource,
    NewsTriageStatus,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    SourceRun,
    SystemAlert,
    WorkerHeartbeat,
)
from tcg_pipeline.news.extraction import NewsExtractionRunResult
from tcg_pipeline.news.ingest import ArticleFetchResult
from tcg_pipeline.news.triage import NewsTriageRunResult
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


def test_scheduled_fire_time_respects_los_angeles_timezone() -> None:
    pytest.importorskip("croniter")

    summer_fire = news_jobs._scheduled_fire_time(
        schedule_cron="0 13 * * *",
        schedule_timezone="America/Los_Angeles",
        last_run_at=None,
        now=datetime(2026, 4, 28, 20, 1, tzinfo=UTC),
        catchup_hours=24,
    )
    winter_fire = news_jobs._scheduled_fire_time(
        schedule_cron="0 13 * * *",
        schedule_timezone="America/Los_Angeles",
        last_run_at=None,
        now=datetime(2026, 1, 15, 21, 1, tzinfo=UTC),
        catchup_hours=24,
    )

    assert summer_fire == datetime(2026, 4, 28, 20, 0, tzinfo=UTC)
    assert winter_fire == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)


def test_worker_settings_validate_positive_intervals() -> None:
    Settings(app_env="test", worker_health_port=0)

    with pytest.raises(ValidationError):
        Settings(app_env="test", worker_heartbeat_interval_seconds=0)
    with pytest.raises(ValidationError):
        Settings(app_env="test", worker_health_max_age_seconds=0)
    with pytest.raises(ValidationError):
        Settings(app_env="test", news_scheduler_interval_seconds=0)
    with pytest.raises(ValidationError):
        Settings(app_env="test", news_scheduler_catchup_hours=0)
    with pytest.raises(ValidationError):
        Settings(app_env="test", worker_health_port=-1)


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


def test_heartbeat_write_refreshes_process_started_at(postgres_session: Session) -> None:
    _ensure_worker_tables(postgres_session)
    worker_name = f"test-worker-{uuid.uuid4().hex}"
    first_started_at = datetime(2026, 4, 28, 11, 0, tzinfo=UTC)
    second_started_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    write_worker_heartbeat(
        postgres_session,
        worker_name=worker_name,
        now=datetime(2026, 4, 28, 11, 1, tzinfo=UTC),
        process_started_at=first_started_at,
    )
    postgres_session.flush()
    write_worker_heartbeat(
        postgres_session,
        worker_name=worker_name,
        now=datetime(2026, 4, 28, 12, 1, tzinfo=UTC),
        process_started_at=second_started_at,
    )
    postgres_session.flush()

    postgres_session.expire_all()
    heartbeat = postgres_session.get(WorkerHeartbeat, worker_name)
    assert heartbeat is not None
    assert heartbeat.process_started_at == second_started_at


def test_unimplemented_news_task_marks_job_failed_and_alerts(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_worker_tables(postgres_session)
    job = ScrapeJob(
        jurisdiction_id=None,
        kind=ScrapeJobKind.NEWS_REEXTRACT.value,
        source_name="news_reextraction",
        target_payload={"article_id": str(uuid.uuid4())},
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
    monkeypatch.setattr(
        news_jobs,
        "write_worker_heartbeat",
        lambda *_args, **_kwargs: pytest.fail(
            "Placeholder task should not write RUNNING heartbeat."
        ),
    )

    with pytest.raises(NotImplementedError, match="not implemented"):
        news_jobs.run_news_reextract_task(str(job.id))

    postgres_session.expire_all()
    refreshed_job = postgres_session.get(ScrapeJob, job.id)
    assert refreshed_job is not None
    assert refreshed_job.status == ScrapeJobStatus.FAILED
    assert "not implemented" in (refreshed_job.error_text or "")
    assert refreshed_job.progress == {
        "message": "News job failed.",
        "error": refreshed_job.error_text,
    }
    alert = postgres_session.execute(
        select(SystemAlert).where(SystemAlert.alert_key == "news_job_failed")
    ).scalar_one()
    assert alert.scope == {
        "job_id": str(job.id),
        "kind": ScrapeJobKind.NEWS_REEXTRACT.value,
    }


def test_raise_system_alert_upserts_active_alert(postgres_session: Session) -> None:
    _ensure_worker_tables(postgres_session)
    alert_key = f"test-alert-{uuid.uuid4().hex}"
    scope = {"source": "test"}

    first = news_jobs.raise_system_alert(
        postgres_session,
        alert_key=alert_key,
        severity="info",
        message="First message.",
        scope=scope,
        detail={"attempt": 1},
    )
    postgres_session.flush()
    second = news_jobs.raise_system_alert(
        postgres_session,
        alert_key=alert_key,
        severity="warning",
        message="Second message.",
        scope=scope,
        detail={"attempt": 2},
    )
    postgres_session.flush()

    assert second.id == first.id
    alert_count = postgres_session.execute(
        select(SystemAlert).where(SystemAlert.alert_key == alert_key)
    ).scalars().all()
    assert len(alert_count) == 1
    postgres_session.expire_all()
    alert = postgres_session.get(SystemAlert, first.id)
    assert alert is not None
    assert alert.severity == "warning"
    assert alert.message == "Second message."
    assert alert.detail == {"attempt": 2}


def test_paste_link_worker_runs_pass0_and_completes_job(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_news_scheduler_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/pass0-worker",
        url_original="https://example.com/pass0-worker?utm_source=test",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.PENDING.value,
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
    )
    postgres_session.add(article)
    postgres_session.flush()
    job = ScrapeJob(
        jurisdiction_id=source.jurisdiction_id,
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        source_name=source.slug,
        target_payload={
            "article_id": str(article.id),
            "url": article.url_original,
            "url_canonical": article.url_canonical,
            "url_hash": article.url_hash,
        },
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
    result = ArticleFetchResult(
        fetch_status=NewsFetchStatus.FETCHED.value,
        final_url=article.url_canonical,
        http_status=200,
        raw_html="<html><body>Article</body></html>",
        raw_html_hash="rawhash",
        body_text="Developer announced a 140-unit project in Los Angeles.",
        body_text_hash="bodyhash",
        title="Developer announces project",
        byline_author="Ava Reporter",
        published_at=datetime(2026, 4, 28, 20, 0, tzinfo=UTC),
        publication_section="Real Estate",
        tags=["housing"],
        external_article_id="article-1",
        paywall_state="open",
    )
    triage_extraction_id = uuid.uuid4()
    extraction_id = uuid.uuid4()

    def fake_triage_runner(article_id: uuid.UUID) -> NewsTriageRunResult:
        assert article_id == article.id
        with task_session_factory() as session:
            triage_article = session.get(NewsArticle, article_id)
            assert triage_article is not None
            assert triage_article.structural_signals is not None
            triage_article.triage_status = NewsTriageStatus.RELEVANT.value
            triage_article.triage_at = datetime(2026, 4, 28, 20, 1, tzinfo=UTC)
            session.commit()
        return NewsTriageRunResult(
            article_id=article_id,
            extraction_id=triage_extraction_id,
            triage_status=NewsTriageStatus.RELEVANT.value,
            relevant=True,
            reason="Article mentions a development project.",
            parse_status="ok",
        )

    news_jobs.run_news_paste_a_link_job(
        job.id,
        fetcher=lambda _url: result,
        triage_runner=fake_triage_runner,
        extraction_runner=lambda article_id: NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=extraction_id,
            relevance="confirmed",
            reference_count=1,
            parse_status="ok",
        ),
    )

    postgres_session.expire_all()
    refreshed_job = postgres_session.get(ScrapeJob, job.id)
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_job is not None
    assert refreshed_article is not None
    assert refreshed_job.status == ScrapeJobStatus.COMPLETED
    assert refreshed_job.source_run_id is not None
    assert refreshed_job.progress["fetch_status"] == NewsFetchStatus.FETCHED.value
    assert refreshed_job.progress["triage_status"] == NewsTriageStatus.RELEVANT.value
    assert refreshed_job.progress["triage_extraction_id"] == str(triage_extraction_id)
    assert refreshed_job.progress["extraction_id"] == str(extraction_id)
    assert refreshed_job.progress["extraction_reference_count"] == 1
    assert refreshed_article.fetch_status == NewsFetchStatus.FETCHED.value
    assert refreshed_article.triage_status == NewsTriageStatus.RELEVANT.value
    assert refreshed_article.fetch_attempts == 1
    assert refreshed_article.title == "Developer announces project"
    assert refreshed_article.body_text == result.body_text
    assert refreshed_article.structural_signals_at is not None
    assert refreshed_article.structural_signals is not None
    assert any(
        signal["extractor"] == "unit_count"
        and signal["canonical"] == 140
        for signal in refreshed_article.structural_signals["signals"]
    )
    source_run = postgres_session.get(SourceRun, refreshed_job.source_run_id)
    assert source_run is not None
    assert source_run.source_name == "news_paste_a_link"
    assert source_run.collection_mode == "single"
    assert source_run.records_pulled == 1
    assert source_run.rows_updated == 1
    assert source_run.errors is None


def test_paste_link_worker_does_not_count_paywall_as_useful_update(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_news_scheduler_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/paywalled-worker",
        url_original="https://example.com/paywalled-worker",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.PENDING.value,
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
    )
    postgres_session.add(article)
    postgres_session.flush()
    job = ScrapeJob(
        jurisdiction_id=source.jurisdiction_id,
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        source_name=source.slug,
        target_payload={
            "article_id": str(article.id),
            "url": article.url_original,
            "url_canonical": article.url_canonical,
            "url_hash": article.url_hash,
        },
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
    result = ArticleFetchResult(
        fetch_status=NewsFetchStatus.PAYWALLED.value,
        final_url=article.url_canonical,
        http_status=200,
        raw_html="<html><body>Subscribe to continue.</body></html>",
        raw_html_hash="rawhash",
        body_text="Subscribe to continue.",
        body_text_hash="bodyhash",
        paywall_state="metered",
        error_text="Article appears paywalled.",
    )

    news_jobs.run_news_paste_a_link_job(
        job.id,
        fetcher=lambda _url: result,
        extraction_runner=None,
    )

    postgres_session.expire_all()
    refreshed_job = postgres_session.get(ScrapeJob, job.id)
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    assert refreshed_job is not None
    assert refreshed_article is not None
    assert refreshed_job.status == ScrapeJobStatus.COMPLETED
    assert refreshed_article.fetch_status == NewsFetchStatus.PAYWALLED.value
    assert refreshed_article.fetch_error_text == "Article appears paywalled."
    source_run = postgres_session.get(SourceRun, refreshed_job.source_run_id)
    assert source_run is not None
    assert source_run.records_pulled == 0
    assert source_run.rows_updated == 0
    assert source_run.errors is None


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


def _news_source(postgres_session: Session, slug: str) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == slug)
    ).scalar_one_or_none()
    if source is None:
        pytest.skip(f"Apply latest Phase D migrations before running worker tests: {slug}")
    return source
