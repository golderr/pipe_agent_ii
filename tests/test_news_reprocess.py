from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsFetchStatus,
    NewsProjectReference,
    NewsSource,
    NewsTriageStatus,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    SourceRun,
)
from tcg_pipeline.news.reprocess import (
    REPROCESS_TRIGGER_SENTINEL,
    ReprocessOutcome,
    StrandedArticle,
    _recovery_context_for_article,
    fetched_since_window,
    find_stranded_articles,
    reprocess_stranded_article,
)
from tcg_pipeline.settings import Settings


def _ensure_news_reprocess_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required = {
        "scrape_jobs",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "agent_runs",
        "news_semantic_interpretations",
    }
    missing = [t for t in required if not inspector.has_table(t)]
    if missing:
        pytest.skip(f"Apply Phase D + AGENT.2 migrations: missing {missing}")


def _news_source(postgres_session: Session, slug: str) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == slug)
    ).scalar_one_or_none()
    if source is None:
        pytest.skip(f"Seed migration for {slug} not applied.")
    return source


def _make_stranded_setup(
    session: Session,
    *,
    with_failed_integrate_job: bool = True,
    with_paste_a_link_job: bool = False,
    integrate_job_status: ScrapeJobStatus = ScrapeJobStatus.FAILED,
    pending_ref_count: int = 1,
    extra_matched_ref_count: int = 0,
    triggers: tuple[str, ...] = ("new_candidate",),
) -> tuple[uuid.UUID, ScrapeJob | None, uuid.UUID | None]:
    """Build a NewsArticle + extraction + refs simulating a stranded state.

    Returns (article_id, failed_job, source_run_id). source_run_id is generated
    here; the test can choose whether to thread it into a ScrapeJob.
    """
    source = _news_source(session, "urbanize_la")
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/stranded-{uuid.uuid4().hex}",
        url_original=f"https://example.com/stranded-{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
        fetched_at=datetime.now(UTC),
    )
    session.add(article)
    session.flush()

    extraction = NewsExtraction(
        article_id=article.id,
        pass_name="extraction",
        triggered_by="scheduled",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash="testhash",
        model="claude-opus-4-7",
        model_provider="anthropic",
        parse_status="ok",
    )
    session.add(extraction)
    session.flush()
    article.current_extraction_id = extraction.id
    session.flush()

    for i in range(pending_ref_count):
        session.add(
            NewsProjectReference(
                article_id=article.id,
                extraction_id=extraction.id,
                reference_index=i,
                match_status="pending",
            )
        )
    for i in range(pending_ref_count, pending_ref_count + extra_matched_ref_count):
        session.add(
            NewsProjectReference(
                article_id=article.id,
                extraction_id=extraction.id,
                reference_index=i,
                match_status="confirmed",
            )
        )
    session.flush()

    source_run_id: uuid.UUID | None = None
    failed_job: ScrapeJob | None = None
    if with_failed_integrate_job or with_paste_a_link_job:
        source_run = SourceRun(
            market="los_angeles",
            source_name="urbanize_la",
            collection_mode="single",
            trigger_type="user_initiated",
            run_timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(source_run)
        session.flush()
        source_run_id = source_run.id
    if with_failed_integrate_job:
        failed_job = ScrapeJob(
            jurisdiction_id=None,
            kind=ScrapeJobKind.NEWS_AGENT_INTEGRATE.value,
            source_name="urbanize_la",
            source_run_id=source_run_id,
            target_payload={
                "article_id": str(article.id),
                "source_run_id": str(source_run_id),
                "parent_job_id": str(uuid.uuid4()),
                "trigger_reasons": list(triggers),
            },
            status=integrate_job_status,
            error_text=(
                "(psycopg.OperationalError) test ssl error"
                if integrate_job_status == ScrapeJobStatus.FAILED
                else None
            ),
            queued_at=datetime.now(UTC) - timedelta(minutes=5),
            completed_at=datetime.now(UTC) - timedelta(minutes=1)
            if integrate_job_status
            in {ScrapeJobStatus.FAILED, ScrapeJobStatus.COMPLETED}
            else None,
        )
        session.add(failed_job)
        session.flush()
    elif with_paste_a_link_job:
        failed_job = ScrapeJob(
            jurisdiction_id=None,
            kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
            source_name="urbanize_la",
            source_run_id=source_run_id,
            target_payload={
                "article_id": str(article.id),
                "url": article.url_canonical,
                "url_canonical": article.url_canonical,
                "url_hash": article.url_hash,
            },
            status=ScrapeJobStatus.FAILED,
            error_text="(psycopg.OperationalError) test ssl error",
            queued_at=datetime.now(UTC) - timedelta(minutes=5),
            completed_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(failed_job)
        session.flush()

    return article.id, failed_job, source_run_id


def test_find_stranded_articles_returns_article_with_failed_integrate_job(
    postgres_session: Session,
) -> None:
    _ensure_news_reprocess_tables(postgres_session)
    article_id, failed_job, _source_run_id = _make_stranded_setup(
        postgres_session,
        with_failed_integrate_job=True,
        triggers=("new_candidate", "pass1_pass2_conflict"),
    )

    results = find_stranded_articles(postgres_session, article_ids=[article_id])
    assert len(results) == 1
    stranded = results[0]
    assert stranded.article_id == article_id
    assert stranded.pending_reference_count == 1
    assert stranded.total_reference_count == 1
    assert stranded.last_failed_job_id == failed_job.id
    assert stranded.last_failed_job_kind == ScrapeJobKind.NEWS_AGENT_INTEGRATE.value
    assert "new_candidate" in stranded.trigger_reasons
    assert "pass1_pass2_conflict" in stranded.trigger_reasons


def test_find_stranded_articles_require_failed_job_excludes_orphans(
    postgres_session: Session,
) -> None:
    _ensure_news_reprocess_tables(postgres_session)
    article_id, _job, _src = _make_stranded_setup(
        postgres_session,
        with_failed_integrate_job=False,
        with_paste_a_link_job=False,
    )

    default = find_stranded_articles(
        postgres_session, article_ids=[article_id]
    )
    assert default == []

    relaxed = find_stranded_articles(
        postgres_session,
        article_ids=[article_id],
        require_failed_job=False,
    )
    assert len(relaxed) == 1
    assert relaxed[0].last_failed_job_id is None
    assert relaxed[0].trigger_reasons == (REPROCESS_TRIGGER_SENTINEL,)
    assert relaxed[0].source_run_id is None


def test_find_stranded_articles_require_failed_job_ignores_completed_jobs(
    postgres_session: Session,
) -> None:
    _ensure_news_reprocess_tables(postgres_session)
    article_id, _job, _src = _make_stranded_setup(
        postgres_session,
        with_failed_integrate_job=True,
        integrate_job_status=ScrapeJobStatus.COMPLETED,
    )

    assert find_stranded_articles(postgres_session, article_ids=[article_id]) == []

    relaxed = find_stranded_articles(
        postgres_session,
        article_ids=[article_id],
        require_failed_job=False,
    )
    assert len(relaxed) == 1
    assert relaxed[0].last_failed_job_id is None
    assert relaxed[0].trigger_reasons == (REPROCESS_TRIGGER_SENTINEL,)


def test_find_stranded_articles_excludes_when_semantic_interpretation_exists(
    postgres_session: Session,
) -> None:
    _ensure_news_reprocess_tables(postgres_session)
    article_id, _job, _src = _make_stranded_setup(
        postgres_session, with_failed_integrate_job=True
    )
    # Add a semantic interpretation so the article no longer qualifies.
    from tcg_pipeline.db.models import NewsSemanticInterpretation

    extraction = postgres_session.execute(
        select(NewsExtraction).where(NewsExtraction.article_id == article_id)
    ).scalar_one()
    sem = NewsSemanticInterpretation(
        article_id=article_id,
        extraction_id=extraction.id,
        prompt_id="interpret_v1",
        prompt_version="v1",
        prompt_hash="testhash",
        model="claude-opus-4-7",
        model_provider="anthropic",
        parse_status="ok",
    )
    postgres_session.add(sem)
    postgres_session.flush()

    assert (
        find_stranded_articles(postgres_session, article_ids=[article_id])
        == []
    )


def test_recovery_context_with_paste_a_link_job_uses_sentinel_trigger(
    postgres_session: Session,
) -> None:
    _ensure_news_reprocess_tables(postgres_session)
    article_id, failed_job, _src = _make_stranded_setup(
        postgres_session,
        with_failed_integrate_job=False,
        with_paste_a_link_job=True,
    )
    context = _recovery_context_for_article(postgres_session, article_id=article_id)
    assert context["last_failed_job_id"] == failed_job.id
    assert context["last_failed_job_kind"] == ScrapeJobKind.NEWS_PASTE_A_LINK.value
    assert context["trigger_reasons"] == (REPROCESS_TRIGGER_SENTINEL,)
    assert context["parent_job_id"] == failed_job.id  # falls back to job.id


def test_reprocess_stranded_article_skips_when_no_source_run_id(
    postgres_session: Session,
) -> None:
    _ensure_news_reprocess_tables(postgres_session)
    settings = Settings()
    article = StrandedArticle(
        article_id=uuid.uuid4(),
        title="t",
        url_canonical="https://example.com/x",
        fetched_at=datetime.now(UTC),
        triage_status="relevant",
        current_extraction_id=None,
        pending_reference_count=1,
        total_reference_count=1,
        source_run_id=None,
        parent_job_id=None,
        trigger_reasons=(REPROCESS_TRIGGER_SENTINEL,),
        last_failed_job_id=None,
        last_failed_job_kind=None,
        last_failed_error_text=None,
    )
    from sqlalchemy.orm import sessionmaker as _sm

    factory = _sm(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    outcome: ReprocessOutcome = reprocess_stranded_article(
        session_factory=factory, article=article, settings=settings
    )
    assert outcome.enqueued is False
    assert outcome.skipped_reason == "no_source_run_id"
    assert outcome.job_id is None


def test_fetched_since_window_returns_offset_from_now() -> None:
    fixed_now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    out = fetched_since_window(days=7, now=fixed_now)
    assert out == fixed_now - timedelta(days=7)
