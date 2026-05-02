from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from scripts import run_d6_urbanize_smoke as smoke
from tcg_pipeline.db.models import (
    Evidence,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsMatchStatus,
    NewsProjectReference,
    NewsSource,
    Priority,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    ScrapeTriggerType,
    SourceRun,
)
from tcg_pipeline.news.urls import canonicalize_news_url


def test_smoke_refuses_production_even_with_non_staging_override() -> None:
    with pytest.raises(RuntimeError, match="production"):
        smoke._validate_environment("production", allow_non_staging=True)

    with pytest.raises(RuntimeError, match="production"):
        smoke._validate_environment("prod", allow_non_staging=True)


def test_cleanup_smoke_token_deletes_smoke_artifacts(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_smoke_tables(postgres_session)
    token = f"cleanup-{uuid.uuid4().hex}"
    source = NewsSource(
        slug=f"smoke-source-{uuid.uuid4().hex}",
        name="Smoke Source",
        base_url="https://la.urbanize.city",
        collector_class="PoliteNewsCollector",
        active=True,
        config={"fetch_path": "polite"},
    )
    postgres_session.add(source)
    postgres_session.flush()
    smoke_url = (
        "https://la.urbanize.city/post/smoke-cleanup-test?"
        f"{smoke.SMOKE_QUERY_KEY}={token}"
    )
    canonical = canonicalize_news_url(smoke_url, source_slug=source.slug)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=canonical.canonical_url,
        url_original=smoke_url,
        url_hash=canonical.url_hash,
        fetch_status=NewsFetchStatus.FETCHED.value,
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
    )
    source_run = SourceRun(
        market="unscoped",
        source_name=source.slug,
        collection_mode="incremental",
        trigger_type=ScrapeTriggerType.SCHEDULED.value,
        records_pulled=1,
    )
    postgres_session.add_all([article, source_run])
    postgres_session.flush()
    evidence = Evidence(
        project_id=None,
        source_type="news_article",
        source_tier=2,
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
        source_record_id=str(article.id),
        raw_data={"article_id": str(article.id)},
    )
    postgres_session.add(evidence)
    postgres_session.flush()
    review_item = ReviewItem(
        project_id=None,
        source_run_id=source_run.id,
        item_type=ReviewItemType.NEW_CANDIDATE,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=Priority.MEDIUM,
        winning_evidence_id=evidence.id,
        payload={"news_context": {"article_id": str(article.id)}},
    )
    job = ScrapeJob(
        kind=ScrapeJobKind.NEWS_SCRAPE.value,
        source_name=source.slug,
        source_run_id=source_run.id,
        trigger_type=ScrapeTriggerType.SCHEDULED,
        status=ScrapeJobStatus.COMPLETED,
        target_payload={"d6_smoke_token": token},
    )
    postgres_session.add_all([review_item, job])
    postgres_session.flush()
    article_id = article.id
    evidence_id = evidence.id
    review_item_id = review_item.id
    job_id = job.id
    source_run_id = source_run.id
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    monkeypatch.setattr(smoke, "get_session_factory", lambda: task_session_factory)

    summary = smoke.cleanup_smoke_token(source_slug=source.slug, token=token)

    assert summary["articles"] == 1
    assert summary["review_items"] == 1
    assert summary["evidence_rows"] == 1
    assert summary["scrape_jobs"] == 1
    assert summary["source_runs"] == 1
    postgres_session.expire_all()
    assert postgres_session.get(NewsArticle, article_id) is None
    assert postgres_session.get(Evidence, evidence_id) is None
    assert postgres_session.get(ReviewItem, review_item_id) is None
    assert postgres_session.get(ScrapeJob, job_id) is None
    assert postgres_session.get(SourceRun, source_run_id) is None


def test_smoke_report_includes_reference_review_and_token_breakdowns(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_smoke_tables(postgres_session)
    token = f"report-{uuid.uuid4().hex}"
    source = NewsSource(
        slug=f"report-source-{uuid.uuid4().hex}",
        name="Report Source",
        base_url="https://la.urbanize.city",
        collector_class="PoliteNewsCollector",
        active=True,
        config={"fetch_path": "polite"},
    )
    postgres_session.add(source)
    postgres_session.flush()
    smoke_url = (
        "https://la.urbanize.city/post/smoke-report-test?"
        f"{smoke.SMOKE_QUERY_KEY}={token}"
    )
    canonical = canonicalize_news_url(smoke_url, source_slug=source.slug)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=canonical.canonical_url,
        url_original=smoke_url,
        url_hash=canonical.url_hash,
        fetch_status=NewsFetchStatus.FETCHED.value,
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
        title="Smoke report article",
    )
    source_run = SourceRun(
        market="unscoped",
        source_name=source.slug,
        collection_mode="incremental",
        trigger_type=ScrapeTriggerType.SCHEDULED.value,
        records_pulled=1,
    )
    postgres_session.add_all([article, source_run])
    postgres_session.flush()
    initial = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="initial",
        prompt_id="extract_v1",
        prompt_version="v1",
        prompt_hash="hash",
        model="claude-opus-4-7",
        parse_status=NewsExtractionParseStatus.OK.value,
        cost_usd=0.40,
        input_tokens_uncached=100,
        input_tokens_cache_creation=200,
        input_tokens_cached=300,
        output_tokens=400,
    )
    current = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.REEXTRACTION.value,
        triggered_by="pass1_pass2_conflict",
        supersedes_extraction_id=initial.id,
        prompt_id="reextract_v1",
        prompt_version="v1",
        prompt_hash="hash2",
        model="claude-opus-4-7",
        parse_status=NewsExtractionParseStatus.OK.value,
        cost_usd=0.50,
        input_tokens_uncached=110,
        input_tokens_cache_creation=0,
        input_tokens_cached=310,
        output_tokens=410,
    )
    postgres_session.add_all([initial, current])
    postgres_session.flush()
    article.current_extraction_id = current.id
    prior_ref = NewsProjectReference(
        article_id=article.id,
        extraction_id=initial.id,
        reference_index=0,
        match_status=NewsMatchStatus.SUPERSEDED_BY_REEXTRACTION.value,
    )
    current_ref = NewsProjectReference(
        article_id=article.id,
        extraction_id=current.id,
        reference_index=0,
        match_status=NewsMatchStatus.NEW_CANDIDATE.value,
    )
    postgres_session.add_all([prior_ref, current_ref])
    postgres_session.flush()
    evidence = Evidence(
        project_id=None,
        source_type="news_article",
        source_tier=2,
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
        source_record_id=str(article.id),
        raw_data={"article_id": str(article.id)},
    )
    postgres_session.add(evidence)
    postgres_session.flush()
    review_item = ReviewItem(
        project_id=None,
        source_run_id=source_run.id,
        item_type=ReviewItemType.NEW_CANDIDATE,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=Priority.MEDIUM,
        winning_evidence_id=evidence.id,
        payload={"news_context": {"article_id": str(article.id)}},
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    current_ref.review_item_id = review_item.id
    postgres_session.flush()
    job = ScrapeJob(
        kind=ScrapeJobKind.NEWS_SCRAPE.value,
        source_name=source.slug,
        source_run_id=source_run.id,
        trigger_type=ScrapeTriggerType.SCHEDULED,
        status=ScrapeJobStatus.COMPLETED,
        target_payload={"d6_smoke_token": token},
    )
    postgres_session.add(job)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    monkeypatch.setattr(smoke, "get_session_factory", lambda: task_session_factory)

    report = smoke._build_report(
        job_id=job.id,
        source_slug=source.slug,
        token=token,
        fixture_rows=[{"slug": "smoke_report", "url": smoke_url}],
        smoke_urls=[smoke_url],
    )

    assert report["reference_breakdown"]["current"] == 1
    assert report["reference_breakdown"]["prior"] == 1
    assert report["review_item_breakdown"]["item_type_counts"] == {"new_candidate": 1}
    assert report["extraction_call_breakdown"]["total_calls"] == 2
    assert report["extraction_call_breakdown"]["input_tokens_cached"] == 610
    article_report = report["articles"][0]
    assert article_report["current_reference_count"] == 1
    assert article_report["prior_reference_count"] == 1
    assert article_report["current_match_status_counts"] == {"new_candidate": 1}
    initial_call = next(
        call for call in article_report["extraction_calls"] if call["pass"] == "extraction"
    )
    assert initial_call["input_tokens_cache_creation"] == 200


def _ensure_smoke_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "evidence",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_sources",
        "review_decisions",
        "review_items",
        "scrape_jobs",
        "source_runs",
    }
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply latest migrations before running smoke tests: {missing}")
