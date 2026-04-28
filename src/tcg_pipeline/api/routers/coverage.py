from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.api.errors import raise_not_implemented
from tcg_pipeline.api.schemas import (
    CoStarUploadResponse,
    CoverageScrapeRequest,
    ScrapeJobResponse,
)
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

router = APIRouter(tags=["coverage"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)
JSON_BODY = Body(default_factory=dict)
COSTAR_FILE = File(...)


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
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ScrapeJobResponse:
    job = enqueue_scrape_job(
        session,
        jurisdiction_id=jurisdiction_id,
        source_name=payload.source_name,
        user=user,
    )
    return _serialize_scrape_job(job)


@router.post("/coverage/{jurisdiction_id}/costar-upload")
def upload_costar_export(
    jurisdiction_id: uuid.UUID,
    file: UploadFile = COSTAR_FILE,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> CoStarUploadResponse:
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
    _user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ScrapeJobResponse:
    job = session.get(ScrapeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scrape job not found.")
    return _serialize_scrape_job(job)


def enqueue_scrape_job(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID,
    source_name: str,
    user: AuthenticatedUser,
) -> ScrapeJob:
    _load_jurisdiction(session, jurisdiction_id)
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
    if registration.source_class == "costar":
        raise HTTPException(status_code=400, detail="Use CoStar upload for CoStar sources.")

    job = ScrapeJob(
        jurisdiction_id=jurisdiction_id,
        source_name=source_name,
        trigger_type=ScrapeTriggerType.USER_INITIATED,
        initiated_by_user_id=user.user_id,
        initiated_by_email=user.email,
        status=ScrapeJobStatus.QUEUED,
        progress={"message": "Queued for scraper worker."},
    )
    session.add(job)
    session.flush()
    return job


def process_costar_upload(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID,
    upload_file: UploadFile,
    user: AuthenticatedUser,
) -> CoStarUpload:
    jurisdiction = _load_jurisdiction(session, jurisdiction_id)
    file_name = Path(upload_file.filename or "costar_upload.xlsx").name
    upload = CoStarUpload(
        jurisdiction_id=jurisdiction_id,
        uploaded_by_user_id=user.user_id,
        uploaded_by_email=user.email,
        file_name=file_name,
        status=CoStarUploadStatus.PROCESSING,
    )
    session.add(upload)
    session.flush()

    suffix = Path(file_name).suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        shutil.copyfileobj(upload_file.file, temp_file)
        temp_path = Path(temp_file.name)
        upload.file_size_bytes = temp_file.tell()

    try:
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
    finally:
        temp_path.unlink(missing_ok=True)

    session.flush()
    return upload


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
