from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_app_settings, get_db_session, require_user
from tcg_pipeline.api.schemas import (
    ResearchArticleCreateRequest,
    ResearchArticleCreateResponse,
    ResearchArticleDetail,
    ResearchArticleDetailResponse,
    ResearchArticleRetryResponse,
    ResearchExtractionSummary,
    ResearchReferenceSummary,
    ScrapeJobResponse,
)
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsFetchStatus,
    NewsProjectReference,
    NewsSource,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    ScrapeTriggerType,
)
from tcg_pipeline.news.urls import canonicalize_news_url
from tcg_pipeline.settings import Settings
from tcg_pipeline.workers.news_jobs import (
    enqueue_news_job_execution,
    run_news_paste_a_link_task,
)

router = APIRouter(tags=["research"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)
APP_SETTINGS = Depends(get_app_settings)
ACTIVE_PASTE_A_LINK_STATUSES = (ScrapeJobStatus.QUEUED, ScrapeJobStatus.RUNNING)
RETRYABLE_FETCH_STATUSES = {
    NewsFetchStatus.FETCH_FAILED.value,
    NewsFetchStatus.PARSE_FAILED.value,
    NewsFetchStatus.PAYWALLED.value,
    NewsFetchStatus.DEAD_LINK.value,
}


@router.post("/research/articles")
def create_research_article(
    payload: ResearchArticleCreateRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    settings: Settings = APP_SETTINGS,
) -> ResearchArticleCreateResponse:
    article, job, existing_article = enqueue_paste_a_link_article(
        session,
        payload=payload,
        user=user,
    )
    session.commit()

    if job is not None:
        _enqueue_paste_a_link_job(
            session,
            job=job,
            background_tasks=background_tasks,
            settings=settings,
            rq_message="Queued for news article ingest.",
            background_message="Queued for API background article ingest.",
        )

    return ResearchArticleCreateResponse(
        article_id=article.id,
        scrape_job_id=job.id if job else None,
        status=job.status.value if job else article.fetch_status,
        existing_article=existing_article,
    )


@router.get("/research/articles/{article_id}")
def get_research_article(
    article_id: uuid.UUID,
    _user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ResearchArticleDetailResponse:
    article = session.get(NewsArticle, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found.")

    scrape_jobs = session.execute(
        select(ScrapeJob)
        .where(
            ScrapeJob.kind.in_(
                [
                    ScrapeJobKind.NEWS_PASTE_A_LINK.value,
                    ScrapeJobKind.NEWS_REEXTRACT.value,
                ]
            ),
            ScrapeJob.target_payload["article_id"].astext == str(article.id),
        )
        .order_by(ScrapeJob.queued_at.desc(), ScrapeJob.id.desc())
    ).scalars()
    extractions = session.execute(
        select(NewsExtraction)
        .where(NewsExtraction.article_id == article.id)
        .order_by(NewsExtraction.created_at.desc(), NewsExtraction.id.desc())
    ).scalars()
    references = session.execute(
        select(NewsProjectReference)
        .where(NewsProjectReference.article_id == article.id)
        .order_by(NewsProjectReference.reference_index.asc(), NewsProjectReference.id.asc())
    ).scalars()
    return ResearchArticleDetailResponse(
        article=_serialize_article(article),
        scrape_jobs=[_serialize_scrape_job(job) for job in scrape_jobs],
        extractions=[_serialize_extraction(extraction) for extraction in extractions],
        references=[_serialize_reference(reference) for reference in references],
    )


@router.post("/research/articles/{article_id}/retry-fetch", include_in_schema=False)
@router.post("/research/articles/{article_id}/refetch")
def refetch_research_article(
    article_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    settings: Settings = APP_SETTINGS,
) -> ResearchArticleRetryResponse:
    article = session.get(NewsArticle, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found.")

    active_job = _active_paste_a_link_job_for_article(session, article_id)
    if active_job is not None:
        return ResearchArticleRetryResponse(
            article_id=article.id,
            scrape_job_id=active_job.id,
            status=_job_status(active_job),
            existing_active_job=True,
        )

    if article.fetch_status not in RETRYABLE_FETCH_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Article fetch is not in a retryable terminal state.",
        )

    article.fetch_status = NewsFetchStatus.PENDING.value
    article.fetch_error_text = None
    article.http_status = None
    article.fetched_at = None
    job = _create_paste_a_link_job(
        session,
        article=article,
        source=article.source,
        user=user,
        force_project_id=_latest_force_project_id_for_article(session, article.id),
        progress_message="Queued for news article refetch.",
    )
    session.commit()
    _enqueue_paste_a_link_job(
        session,
        job=job,
        background_tasks=background_tasks,
        settings=settings,
        rq_message="Queued for news article refetch.",
        background_message="Queued for API background article refetch.",
    )
    return ResearchArticleRetryResponse(
        article_id=article.id,
        scrape_job_id=job.id,
        status=_job_status(job),
        existing_active_job=False,
    )


def enqueue_paste_a_link_article(
    session: Session,
    *,
    payload: ResearchArticleCreateRequest,
    user: AuthenticatedUser,
) -> tuple[NewsArticle, ScrapeJob | None, bool]:
    if payload.force_reextract:
        raise HTTPException(
            status_code=400,
            detail="Manual re-extraction endpoint is not yet implemented.",
        )

    try:
        canonical_url = canonicalize_news_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing_article = session.execute(
        select(NewsArticle).where(NewsArticle.url_hash == canonical_url.url_hash)
    ).scalar_one_or_none()
    if existing_article is not None:
        return existing_article, None, True

    source = _load_news_source(session, canonical_url.source_slug)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=canonical_url.canonical_url,
        url_original=canonical_url.original_url,
        url_hash=canonical_url.url_hash,
        fetch_status=NewsFetchStatus.PENDING.value,
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        ingested_by_user_id=user.user_id,
        notes=payload.note,
    )
    try:
        with session.begin_nested():
            session.add(article)
            session.flush()
    except IntegrityError:
        raced_article = session.execute(
            select(NewsArticle).where(NewsArticle.url_hash == canonical_url.url_hash)
        ).scalar_one_or_none()
        if raced_article is None:
            raise
        return raced_article, None, True

    job = _create_paste_a_link_job(
        session,
        article=article,
        source=source,
        user=user,
        force_project_id=payload.force_project_id,
        progress_message="Queued for news article ingest.",
    )
    return article, job, False


def _create_paste_a_link_job(
    session: Session,
    *,
    article: NewsArticle,
    source: NewsSource,
    user: AuthenticatedUser,
    force_project_id: uuid.UUID | str | None,
    progress_message: str,
) -> ScrapeJob:
    job = ScrapeJob(
        jurisdiction_id=source.jurisdiction_id,
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        source_name=source.slug,
        trigger_type=ScrapeTriggerType.USER_INITIATED,
        initiated_by_user_id=user.user_id,
        initiated_by_email=user.email,
        status=ScrapeJobStatus.QUEUED,
        target_payload={
            "article_id": str(article.id),
            "url": article.url_original,
            "url_canonical": article.url_canonical,
            "url_hash": article.url_hash,
            "force_project_id": str(force_project_id) if force_project_id else None,
        },
        progress={"message": progress_message},
    )
    session.add(job)
    session.flush()
    return job


def _enqueue_paste_a_link_job(
    session: Session,
    *,
    job: ScrapeJob,
    background_tasks: BackgroundTasks,
    settings: Settings,
    rq_message: str,
    background_message: str,
) -> None:
    queued = enqueue_news_job_execution(
        job.id,
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        settings=settings,
    )
    job.progress = {
        "message": rq_message if queued else background_message,
        "queue_backend": "rq" if queued else "background",
    }
    session.commit()
    if not queued:
        background_tasks.add_task(run_news_paste_a_link_task, str(job.id))


def _active_paste_a_link_job_for_article(
    session: Session,
    article_id: uuid.UUID,
) -> ScrapeJob | None:
    return session.execute(
        select(ScrapeJob)
        .where(
            ScrapeJob.kind == ScrapeJobKind.NEWS_PASTE_A_LINK.value,
            ScrapeJob.status.in_(ACTIVE_PASTE_A_LINK_STATUSES),
            ScrapeJob.target_payload["article_id"].astext == str(article_id),
        )
        .order_by(ScrapeJob.queued_at.desc(), ScrapeJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_force_project_id_for_article(
    session: Session,
    article_id: uuid.UUID,
) -> str | None:
    job = session.execute(
        select(ScrapeJob)
        .where(
            ScrapeJob.kind == ScrapeJobKind.NEWS_PASTE_A_LINK.value,
            ScrapeJob.target_payload["article_id"].astext == str(article_id),
        )
        .order_by(ScrapeJob.queued_at.desc(), ScrapeJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if job is None or not isinstance(job.target_payload, dict):
        return None
    force_project_id = job.target_payload.get("force_project_id")
    return force_project_id if isinstance(force_project_id, str) else None


def _load_news_source(session: Session, source_slug: str) -> NewsSource:
    source = session.execute(
        select(NewsSource).where(NewsSource.slug == source_slug)
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=500,
            detail=f"News source '{source_slug}' is not configured.",
        )
    return source


def _serialize_article(article: NewsArticle) -> ResearchArticleDetail:
    return ResearchArticleDetail(
        id=article.id,
        news_source_id=article.news_source_id,
        source_name=article.source.slug,
        url_canonical=article.url_canonical,
        url_original=article.url_original,
        fetch_status=article.fetch_status,
        fetch_attempts=article.fetch_attempts,
        fetched_at=_iso(article.fetched_at),
        fetch_error_text=article.fetch_error_text,
        http_status=article.http_status,
        title=article.title,
        byline_author=article.byline_author,
        published_at=_iso(article.published_at),
        publication_section=article.publication_section,
        tags=article.tags,
        external_article_id=article.external_article_id,
        language=article.language,
        paywall_state=article.paywall_state,
        body_text=article.body_text,
        body_text_hash=article.body_text_hash,
        raw_html_hash=article.raw_html_hash,
        structural_signals_at=_iso(article.structural_signals_at),
        triage_status=article.triage_status,
        triage_at=_iso(article.triage_at),
        triage_extraction_id=article.triage_extraction_id,
        current_extraction_id=article.current_extraction_id,
        current_extraction_version=article.current_extraction_version,
        ingest_method=article.ingest_method,
        ingested_by_user_id=article.ingested_by_user_id,
        notes=article.notes,
        created_at=_iso_required(article.created_at),
        updated_at=_iso_required(article.updated_at),
    )


def _serialize_extraction(extraction: NewsExtraction) -> ResearchExtractionSummary:
    return ResearchExtractionSummary(
        id=extraction.id,
        pass_name=extraction.pass_name,
        triggered_by=extraction.triggered_by,
        prompt_id=extraction.prompt_id,
        prompt_version=extraction.prompt_version,
        model=extraction.model,
        parse_status=extraction.parse_status,
        created_at=_iso_required(extraction.created_at),
    )


def _serialize_reference(reference: NewsProjectReference) -> ResearchReferenceSummary:
    return ResearchReferenceSummary(
        id=reference.id,
        extraction_id=reference.extraction_id,
        reference_index=reference.reference_index,
        candidate_name=reference.candidate_name,
        candidate_address=reference.candidate_address,
        candidate_developer=reference.candidate_developer,
        match_status=reference.match_status,
        matched_project_id=reference.matched_project_id,
    )


def _serialize_scrape_job(job: ScrapeJob) -> ScrapeJobResponse:
    trigger_type = (
        job.trigger_type.value if hasattr(job.trigger_type, "value") else job.trigger_type
    )
    return ScrapeJobResponse(
        id=job.id,
        jurisdiction_id=job.jurisdiction_id,
        kind=job.kind,
        source_name=job.source_name,
        target_payload=job.target_payload,
        trigger_type=trigger_type,
        initiated_by_user_id=job.initiated_by_user_id,
        initiated_by_email=job.initiated_by_email,
        status=_job_status(job),
        queued_at=_iso_required(job.queued_at),
        started_at=_iso(job.started_at),
        completed_at=_iso(job.completed_at),
        source_run_id=job.source_run_id,
        error_text=job.error_text,
        progress=job.progress,
    )


def _job_status(job: ScrapeJob) -> str:
    return job.status.value if hasattr(job.status, "value") else job.status


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _iso_required(value)


def _iso_required(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
