from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_app_settings, get_db_session, require_user
from tcg_pipeline.api.errors import raise_not_implemented
from tcg_pipeline.api.schemas import (
    CoStarUploadResponse,
    CoverageScrapeRequest,
    ScrapeJobResponse,
    ScrapeWorkerHealthResponse,
)
from tcg_pipeline.collectors.base import CollectionMode, CollectionRequest, RawRecord
from tcg_pipeline.collectors.factory import build_collector
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    CoStarUpload,
    CoStarUploadStatus,
    Jurisdiction,
    ScrapeJob,
    ScrapeJobStatus,
    ScrapeTriggerType,
    SourceRegistration,
    SourceRun,
)
from tcg_pipeline.db.seed import CoStarPersistResult, seed_costar_workbooks
from tcg_pipeline.ingesters.costar import COSTAR_SOURCE_NAME, CoStarImportResult
from tcg_pipeline.market_config import SourceConfig, get_market_config
from tcg_pipeline.settings import Settings
from tcg_pipeline.source_adapters import ADAPTER_BUILDERS
from tcg_pipeline.workers.scrape_jobs import enqueue_scrape_job_execution, scrape_queue_status

router = APIRouter(tags=["coverage"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)
APP_SETTINGS = Depends(get_app_settings)
JSON_BODY = Body(default_factory=dict)
COSTAR_FILE = File(...)
COSTAR_SOURCE_CLASS = "costar"
MAX_COSTAR_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_MULTIPART_UPLOAD_BYTES = MAX_COSTAR_UPLOAD_BYTES + 1_000_000
UPLOAD_COPY_CHUNK_BYTES = 1024 * 1024
ACTIVE_SCRAPE_JOB_STATUSES = (ScrapeJobStatus.QUEUED, ScrapeJobStatus.RUNNING)
INLINE_REFRESH_UNAVAILABLE = (
    "Refresh is not available for this source until scraper worker support is deployed."
)


@dataclass(slots=True)
class ScrapeExecutionPlan:
    job_id: uuid.UUID
    jurisdiction_id: uuid.UUID
    source_name: str
    market_slug: str
    source_config: SourceConfig
    request: CollectionRequest


@router.post("/coverage/{jurisdiction_id}/pin")
def toggle_jurisdiction_pin(
    jurisdiction_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"jurisdiction pin toggle for {jurisdiction_id}")


@router.post("/coverage/{jurisdiction_id}/scrape")
def enqueue_scrape(
    jurisdiction_id: uuid.UUID,
    payload: CoverageScrapeRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    settings: Settings = APP_SETTINGS,
) -> ScrapeJobResponse:
    job = enqueue_scrape_job(
        session,
        jurisdiction_id=jurisdiction_id,
        source_name=payload.source_name,
        user=user,
        queue_backend="rq" if settings.redis_url else "background",
    )
    session.commit()
    if not enqueue_scrape_job_execution(job.id, settings=settings):
        background_tasks.add_task(run_scrape_job, job.id)
    return _serialize_scrape_job(job)


@router.post("/coverage/{jurisdiction_id}/costar-upload")
def upload_costar_export(
    jurisdiction_id: uuid.UUID,
    request: Request,
    file: UploadFile = COSTAR_FILE,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> CoStarUploadResponse:
    validate_costar_upload_request(request)
    upload = process_costar_upload(
        session,
        jurisdiction_id=jurisdiction_id,
        upload_file=file,
        user=user,
    )
    return _serialize_costar_upload(upload)


@router.get("/scrape_jobs/{job_id}")
def get_scrape_job(
    job_id: uuid.UUID,
    jurisdiction_id: uuid.UUID | None = None,
    _user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ScrapeJobResponse:
    job = session.get(ScrapeJob, job_id)
    if job is None or (jurisdiction_id is not None and job.jurisdiction_id != jurisdiction_id):
        raise HTTPException(status_code=404, detail="Scrape job not found.")
    return _serialize_scrape_job(job)


@router.get("/coverage/{jurisdiction_id}/scrape_jobs")
def list_scrape_jobs(
    jurisdiction_id: uuid.UUID,
    source_name: str | None = None,
    limit: int = 5,
    _user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> list[ScrapeJobResponse]:
    bounded_limit = min(max(limit, 1), 25)
    query = (
        select(ScrapeJob)
        .where(ScrapeJob.jurisdiction_id == jurisdiction_id)
        .order_by(ScrapeJob.queued_at.desc(), ScrapeJob.id.desc())
        .limit(bounded_limit)
    )
    if source_name:
        query = query.where(ScrapeJob.source_name == source_name)
    return [_serialize_scrape_job(job) for job in session.execute(query).scalars().all()]


@router.get("/scrape_workers/health")
def get_scrape_worker_health(
    _user: AuthenticatedUser = AUTH_USER,
    settings: Settings = APP_SETTINGS,
) -> ScrapeWorkerHealthResponse:
    status = scrape_queue_status(settings=settings)
    return ScrapeWorkerHealthResponse(
        configured=status.configured,
        available=status.available,
        queue_name=status.queue_name,
        queued_jobs=status.queued_jobs,
        started_jobs=status.started_jobs,
        failed_jobs=status.failed_jobs,
        worker_count=status.worker_count,
        error=status.error,
    )


def enqueue_scrape_job(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID,
    source_name: str,
    user: AuthenticatedUser,
    queue_backend: str = "background",
) -> ScrapeJob:
    jurisdiction = _load_jurisdiction(session, jurisdiction_id)
    registration = _load_source_registration(
        session,
        jurisdiction_id=jurisdiction_id,
        source_name=source_name,
    )
    if registration.source_class == COSTAR_SOURCE_CLASS:
        raise HTTPException(status_code=400, detail="Use CoStar upload for CoStar sources.")
    _resolve_inline_source_config(jurisdiction=jurisdiction, registration=registration)

    existing_job = _load_active_scrape_job(
        session,
        jurisdiction_id=jurisdiction_id,
        source_name=source_name,
    )
    if existing_job is not None:
        return existing_job

    job = ScrapeJob(
        jurisdiction_id=jurisdiction_id,
        source_name=source_name,
        trigger_type=ScrapeTriggerType.USER_INITIATED,
        initiated_by_user_id=user.user_id,
        initiated_by_email=user.email,
        status=ScrapeJobStatus.QUEUED,
        progress={
            "message": "Queued for scraper worker."
            if queue_backend == "rq"
            else "Queued for API background scrape.",
            "queue_backend": queue_backend,
        },
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="A scrape job is already queued or running for this source.",
        ) from exc
    return job


def validate_costar_upload_request(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        request_size = int(content_length)
    except ValueError:
        return
    if request_size > MAX_MULTIPART_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="CoStar uploads must be 50 MB or smaller.",
        )


def process_costar_upload(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID,
    upload_file: UploadFile,
    user: AuthenticatedUser,
) -> CoStarUpload:
    jurisdiction = _load_jurisdiction(session, jurisdiction_id)
    file_name = Path(upload_file.filename or "costar_upload.xlsx").name
    suffix = Path(file_name).suffix or ".xlsx"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            file_size_bytes = _copy_upload_to_temp(upload_file, temp_file)

        upload = CoStarUpload(
            jurisdiction_id=jurisdiction_id,
            uploaded_by_user_id=user.user_id,
            uploaded_by_email=user.email,
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            status=CoStarUploadStatus.PROCESSING,
        )
        session.add(upload)
        session.flush()

        try:
            # Keep the audit row outside the savepoint so failed imports still persist.
            with session.begin_nested():
                import_result, persist_result = seed_costar_workbooks(
                    session,
                    [temp_path],
                    market=jurisdiction.market.slug,
                    source_name=COSTAR_SOURCE_NAME,
                )
                source_run = _record_costar_source_run(
                    session,
                    jurisdiction=jurisdiction,
                    user=user,
                    import_result=import_result,
                    persist_result=persist_result,
                )
            upload.row_count = import_result.imported_count
            upload.source_run_id = source_run.id
            upload.status = CoStarUploadStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001 - failure details are persisted for upload audit.
            upload.status = CoStarUploadStatus.FAILED
            upload.error_text = str(exc)

        session.flush()
        return upload
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def run_scrape_job(job_id: uuid.UUID) -> None:
    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            plan = start_scrape_job(session, job_id=job_id)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - background tasks must persist failures.
        _record_scrape_job_failure(job_id, exc)
        return

    if plan is None:
        return

    try:
        raw_records = collect_scrape_records(plan)
    except Exception as exc:  # noqa: BLE001 - collector failures are job outcomes.
        _record_scrape_job_failure(job_id, exc)
        return

    with session_factory() as session:
        try:
            complete_scrape_job(session, plan=plan, raw_records=raw_records)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - persistence failures are job outcomes.
            session.rollback()
            _record_scrape_job_failure(job_id, exc)


def start_scrape_job(session: Session, *, job_id: uuid.UUID) -> ScrapeExecutionPlan | None:
    job = session.get(ScrapeJob, job_id)
    if job is None or job.status != ScrapeJobStatus.QUEUED:
        return None
    jurisdiction = _load_jurisdiction(session, job.jurisdiction_id)
    registration = _load_source_registration(
        session,
        jurisdiction_id=job.jurisdiction_id,
        source_name=job.source_name,
    )
    source_config = _resolve_inline_source_config(
        jurisdiction=jurisdiction,
        registration=registration,
    )
    request = _collection_request_for_source(
        session,
        market_slug=jurisdiction.market.slug,
        source_config=source_config,
    )
    now = datetime.now(UTC)
    job.status = ScrapeJobStatus.RUNNING
    job.started_at = now
    job.progress = {
        "message": "Collecting source records.",
        "collection_mode": request.mode.value,
        "updated_since": request.updated_since.isoformat() if request.updated_since else None,
    }
    session.flush()
    return ScrapeExecutionPlan(
        job_id=job.id,
        jurisdiction_id=jurisdiction.id,
        source_name=job.source_name,
        market_slug=jurisdiction.market.slug,
        source_config=source_config,
        request=request,
    )


def collect_scrape_records(plan: ScrapeExecutionPlan) -> list[RawRecord]:
    collector = build_collector(plan.source_config, market=plan.market_slug)
    return asyncio.run(collector.collect(plan.request))


def complete_scrape_job(
    session: Session,
    *,
    plan: ScrapeExecutionPlan,
    raw_records: Sequence[RawRecord],
) -> ScrapeJob:
    job = session.get(ScrapeJob, plan.job_id)
    if job is None:
        raise RuntimeError("Scrape job disappeared before completion.")
    if job.status != ScrapeJobStatus.RUNNING:
        return job

    persist_result = persist_collected_records(
        session,
        market=plan.market_slug,
        source_name=plan.source_name,
        raw_records=list(raw_records),
        collection_mode=plan.request.mode.value,
        incremental_since=plan.request.updated_since,
        create_new_candidates=plan.source_config.create_new_candidates,
    )
    source_run = session.get(SourceRun, persist_result.source_run_id)
    if source_run is None:
        raise RuntimeError("Scrape source run was not persisted.")
    now = datetime.now(UTC)
    source_run.jurisdiction_id = plan.jurisdiction_id
    source_run.trigger_type = "user_initiated"
    source_run.initiated_by_user_id = job.initiated_by_user_id
    source_run.finished_at = now

    job.status = ScrapeJobStatus.COMPLETED
    job.completed_at = now
    job.source_run_id = source_run.id
    job.progress = {
        "message": "Scrape completed.",
        "records_pulled": persist_result.records_pulled,
        "matched_existing_projects": persist_result.matched_existing_projects,
        "inserted_source_records": persist_result.inserted_source_records,
        "updated_source_records": persist_result.updated_source_records,
        "new_candidate_review_items": persist_result.new_candidate_review_items,
        "status_change_review_items": persist_result.status_change_review_items,
        "possible_match_review_items": persist_result.possible_match_review_items,
    }
    session.flush()
    return job


def mark_scrape_job_failed(
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
    job.error_text = _exception_detail(error)
    job.progress = {"message": "Scrape failed."}
    session.flush()
    return job


def _record_scrape_job_failure(job_id: uuid.UUID, error: Exception) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            mark_scrape_job_failed(session, job_id=job_id, error=error)
            session.commit()
        except Exception:
            session.rollback()


def _load_source_registration(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID,
    source_name: str,
) -> SourceRegistration:
    registration = session.execute(
        select(SourceRegistration).where(
            SourceRegistration.jurisdiction_id == jurisdiction_id,
            SourceRegistration.source_name == source_name,
        )
    ).scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=404, detail="Source registration not found.")
    if not registration.active:
        raise HTTPException(status_code=400, detail="Source registration is inactive.")
    return registration


def _load_active_scrape_job(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID,
    source_name: str,
) -> ScrapeJob | None:
    return session.execute(
        select(ScrapeJob)
        .where(
            ScrapeJob.jurisdiction_id == jurisdiction_id,
            ScrapeJob.source_name == source_name,
            ScrapeJob.status.in_(ACTIVE_SCRAPE_JOB_STATUSES),
        )
        .order_by(ScrapeJob.queued_at.desc(), ScrapeJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _resolve_inline_source_config(
    *,
    jurisdiction: Jurisdiction,
    registration: SourceRegistration,
) -> SourceConfig:
    try:
        source_config = get_market_config(jurisdiction.market.slug).get_source(
            registration.source_name
        )
    except (FileNotFoundError, KeyError, ValueError):
        source_config = _source_config_from_registration(registration)
    if (
        source_config.collector != "socrata"
        or source_config.adapter_name not in ADAPTER_BUILDERS
    ):
        raise HTTPException(status_code=400, detail=INLINE_REFRESH_UNAVAILABLE)
    return source_config


def _source_config_from_registration(registration: SourceRegistration) -> SourceConfig:
    config = dict(registration.config or {})
    if not config:
        raise HTTPException(status_code=400, detail=INLINE_REFRESH_UNAVAILABLE)
    return SourceConfig.model_validate({"name": registration.source_name, **config})


def _collection_request_for_source(
    session: Session,
    *,
    market_slug: str,
    source_config: SourceConfig,
) -> CollectionRequest:
    updated_since = _resolve_incremental_cursor(
        session,
        market=market_slug,
        source_name=source_config.name,
        overlap_hours=source_config.incremental_overlap_hours,
    )
    if updated_since is None:
        return CollectionRequest(mode=CollectionMode.FULL)
    return CollectionRequest(mode=CollectionMode.INCREMENTAL, updated_since=updated_since)


def _resolve_incremental_cursor(
    session: Session,
    *,
    market: str,
    source_name: str,
    overlap_hours: int,
) -> datetime | None:
    max_seen_updated_at = session.execute(
        select(func.max(SourceRun.source_max_updated_at)).where(
            SourceRun.market == market,
            SourceRun.source_name == source_name,
            SourceRun.source_max_updated_at.is_not(None),
        )
    ).scalar_one()
    if max_seen_updated_at is None:
        return None
    return max_seen_updated_at - timedelta(hours=overlap_hours)


def _copy_upload_to_temp(upload_file: UploadFile, temp_file: Any) -> int:
    copied = 0
    while True:
        chunk = upload_file.file.read(UPLOAD_COPY_CHUNK_BYTES)
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > MAX_COSTAR_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="CoStar uploads must be 50 MB or smaller.",
            )
        temp_file.write(chunk)


def _exception_detail(error: Exception) -> str:
    if isinstance(error, HTTPException):
        return str(error.detail)
    return str(error)


def _load_jurisdiction(session: Session, jurisdiction_id: uuid.UUID) -> Jurisdiction:
    jurisdiction = session.get(Jurisdiction, jurisdiction_id)
    if jurisdiction is None:
        raise HTTPException(status_code=404, detail="Jurisdiction not found.")
    return jurisdiction


def _record_costar_source_run(
    session: Session,
    *,
    jurisdiction: Jurisdiction,
    user: AuthenticatedUser,
    import_result: CoStarImportResult,
    persist_result: CoStarPersistResult,
) -> SourceRun:
    source_run = SourceRun(
        market=jurisdiction.market.slug,
        jurisdiction_id=jurisdiction.id,
        source_name=COSTAR_SOURCE_NAME,
        collection_mode="full",
        trigger_type="user_initiated",
        initiated_by_user_id=user.user_id,
        finished_at=datetime.now(UTC),
        records_pulled=import_result.imported_count,
        rows_inserted=persist_result.inserted_projects,
        rows_updated=persist_result.matched_existing_projects,
        rows_unchanged=0,
        errors=_costar_issue_text(import_result),
    )
    session.add(source_run)
    session.flush()
    return source_run


def _costar_issue_text(import_result: CoStarImportResult) -> str | None:
    issue_count = len(import_result.issues)
    skipped_count = (
        import_result.missing_property_id_rows
        + len(import_result.skipped_property_ids)
        + len(import_result.duplicate_property_ids)
    )
    if issue_count == 0 and skipped_count == 0:
        return None
    return (
        f"{issue_count} issues, {skipped_count} skipped rows "
        f"while importing {import_result.imported_count} CoStar rows."
    )


def _serialize_scrape_job(job: ScrapeJob) -> ScrapeJobResponse:
    return ScrapeJobResponse(
        id=job.id,
        jurisdiction_id=job.jurisdiction_id,
        source_name=job.source_name,
        trigger_type=job.trigger_type.value,
        initiated_by_user_id=job.initiated_by_user_id,
        initiated_by_email=job.initiated_by_email,
        status=job.status.value,
        queued_at=job.queued_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        source_run_id=job.source_run_id,
        error_text=job.error_text,
        progress=job.progress,
    )


def _serialize_costar_upload(upload: CoStarUpload) -> CoStarUploadResponse:
    return CoStarUploadResponse(
        id=upload.id,
        jurisdiction_id=upload.jurisdiction_id,
        file_name=upload.file_name,
        file_size_bytes=upload.file_size_bytes,
        row_count=upload.row_count,
        source_run_id=upload.source_run_id,
        status=upload.status.value,
        error_text=upload.error_text,
    )
