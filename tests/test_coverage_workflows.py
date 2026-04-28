from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.routers import coverage as coverage_router
from tcg_pipeline.collectors.base import CollectionMode, RawRecord
from tcg_pipeline.db.models import (
    CoStarUploadStatus,
    Jurisdiction,
    Market,
    Project,
    ScrapeJob,
    ScrapeJobStatus,
    SourceRegistration,
    SourceRun,
)
from tcg_pipeline.db.seed import CoStarPersistResult

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_enqueue_scrape_job_returns_existing_active_job(postgres_session: Session) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)
    _source_registration(postgres_session, jurisdiction, "ladbs_permits")
    user = _user(postgres_session)

    first = coverage_router.enqueue_scrape_job(
        postgres_session,
        jurisdiction_id=jurisdiction.id,
        source_name="ladbs_permits",
        user=user,
    )
    second = coverage_router.enqueue_scrape_job(
        postgres_session,
        jurisdiction_id=jurisdiction.id,
        source_name="ladbs_permits",
        user=user,
    )
    postgres_session.flush()

    active_count = postgres_session.execute(
        select(func.count())
        .select_from(ScrapeJob)
        .where(
            ScrapeJob.jurisdiction_id == jurisdiction.id,
            ScrapeJob.source_name == "ladbs_permits",
            ScrapeJob.status.in_([ScrapeJobStatus.QUEUED, ScrapeJobStatus.RUNNING]),
        )
    ).scalar_one()

    assert second.id == first.id
    assert active_count == 1


def test_enqueue_scrape_job_rejects_unsupported_inline_source(
    postgres_session: Session,
) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)
    _source_registration(postgres_session, jurisdiction, "la_case_reports")

    with pytest.raises(HTTPException) as exc_info:
        coverage_router.enqueue_scrape_job(
            postgres_session,
            jurisdiction_id=jurisdiction.id,
            source_name="la_case_reports",
            user=_user(postgres_session),
        )

    assert exc_info.value.status_code == 400
    assert "worker support" in str(exc_info.value.detail)


def test_start_scrape_job_uses_incremental_cursor(postgres_session: Session) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)
    _source_registration(postgres_session, jurisdiction, "ladbs_permits")
    cursor = datetime(2030, 4, 20, 12, 0, tzinfo=UTC)
    postgres_session.add(
        SourceRun(
            market="los_angeles",
            source_name="ladbs_permits",
            collection_mode="incremental",
            source_max_updated_at=cursor,
        )
    )
    job = coverage_router.enqueue_scrape_job(
        postgres_session,
        jurisdiction_id=jurisdiction.id,
        source_name="ladbs_permits",
        user=_user(postgres_session),
    )

    plan = coverage_router.start_scrape_job(postgres_session, job_id=job.id)
    postgres_session.refresh(job)

    assert plan is not None
    assert plan.request.mode == CollectionMode.INCREMENTAL
    assert plan.request.updated_since == cursor - timedelta(hours=24)
    assert job.status == ScrapeJobStatus.RUNNING
    assert job.progress["collection_mode"] == "incremental"


def test_complete_scrape_job_persists_records_and_updates_job(
    postgres_session: Session,
) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)
    _source_registration(postgres_session, jurisdiction, "ladbs_permits")
    project = Project(
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        raw_addresses=["7270 Manchester Ave"],
        market="los_angeles",
        jurisdiction_id=jurisdiction.id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
    postgres_session.add(project)
    postgres_session.flush()
    user = _user(postgres_session)
    job = coverage_router.enqueue_scrape_job(
        postgres_session,
        jurisdiction_id=jurisdiction.id,
        source_name="ladbs_permits",
        user=user,
    )
    plan = coverage_router.start_scrape_job(postgres_session, job_id=job.id)
    assert plan is not None

    coverage_router.complete_scrape_job(
        postgres_session,
        plan=plan,
        raw_records=[
            RawRecord(
                source_name="ladbs_permits",
                source_record_id="11010-10000-02451",
                raw_payload={"pcis_permit": "11010-10000-02451"},
                canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
                identifiers={"permit_number": ["11010-10000-02451"]},
                mapped_fields={"total_units": 260},
                source_row_id="row-57hi~6iij-sky2",
                source_updated_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
                source_row_hash="abc123",
            )
        ],
    )
    postgres_session.refresh(job)
    source_run = postgres_session.get(SourceRun, job.source_run_id)

    assert job.status == ScrapeJobStatus.COMPLETED
    assert job.progress["records_pulled"] == 1
    assert source_run is not None
    assert source_run.jurisdiction_id == jurisdiction.id
    assert source_run.trigger_type == "user_initiated"
    assert source_run.initiated_by_user_id == user.user_id
    assert source_run.finished_at is not None


def test_mark_scrape_job_failed_records_error(postgres_session: Session) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)
    job = ScrapeJob(
        jurisdiction_id=jurisdiction.id,
        source_name="ladbs_permits",
        status=ScrapeJobStatus.RUNNING,
    )
    postgres_session.add(job)
    postgres_session.flush()

    coverage_router.mark_scrape_job_failed(
        postgres_session,
        job_id=job.id,
        error=RuntimeError("collector failed"),
    )
    postgres_session.refresh(job)

    assert job.status == ScrapeJobStatus.FAILED
    assert job.error_text == "collector failed"
    assert job.completed_at is not None
    assert job.progress == {"message": "Scrape failed."}


def test_process_costar_upload_records_success_audit(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)

    def fake_seed_costar_workbooks(
        _session: Session,
        paths: list[Any],
        **kwargs: Any,
    ) -> tuple[Any, CoStarPersistResult]:
        assert paths
        assert kwargs["market"] == "los_angeles"
        return (
            SimpleNamespace(
                imported_count=2,
                issues=[],
                missing_property_id_rows=0,
                skipped_property_ids=[],
                duplicate_property_ids=[],
            ),
            CoStarPersistResult(inserted_projects=1, matched_existing_projects=1),
        )

    monkeypatch.setattr(coverage_router, "seed_costar_workbooks", fake_seed_costar_workbooks)

    upload = coverage_router.process_costar_upload(
        postgres_session,
        jurisdiction_id=jurisdiction.id,
        upload_file=UploadFile(filename="costar.xlsx", file=io.BytesIO(b"fake workbook")),
        user=_user(postgres_session),
    )
    source_run = postgres_session.get(SourceRun, upload.source_run_id)

    assert upload.status == CoStarUploadStatus.COMPLETED
    assert upload.file_size_bytes == len(b"fake workbook")
    assert upload.row_count == 2
    assert source_run is not None
    assert source_run.jurisdiction_id == jurisdiction.id
    assert source_run.records_pulled == 2


def test_process_costar_upload_preserves_failed_audit(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_coverage_tables(postgres_session)
    jurisdiction = _jurisdiction(postgres_session)

    def fake_seed_costar_workbooks(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, CoStarPersistResult]:
        raise RuntimeError("bad workbook")

    monkeypatch.setattr(coverage_router, "seed_costar_workbooks", fake_seed_costar_workbooks)

    upload = coverage_router.process_costar_upload(
        postgres_session,
        jurisdiction_id=jurisdiction.id,
        upload_file=UploadFile(filename="costar.xlsx", file=io.BytesIO(b"fake workbook")),
        user=_user(postgres_session),
    )

    assert upload.status == CoStarUploadStatus.FAILED
    assert upload.file_size_bytes == len(b"fake workbook")
    assert upload.source_run_id is None
    assert upload.error_text == "bad workbook"


def test_copy_upload_to_temp_rejects_oversized_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(coverage_router, "MAX_COSTAR_UPLOAD_BYTES", 5)
    upload = UploadFile(filename="costar.xlsx", file=io.BytesIO(b"123456"))

    with (
        (tmp_path / "costar.xlsx").open("wb") as temp_file,
        pytest.raises(HTTPException) as exc_info,
    ):
        coverage_router._copy_upload_to_temp(upload, temp_file)

    assert exc_info.value.status_code == 413


def _user(postgres_session: Session | None = None) -> AuthenticatedUser:
    user_id = _existing_auth_user_id(postgres_session) if postgres_session is not None else None
    return AuthenticatedUser(
        user_id=user_id or USER_ID,
        email="allowed@example.com",
        role="authenticated",
        claims={"sub": str(user_id or USER_ID), "email": "allowed@example.com"},
    )


def _existing_auth_user_id(postgres_session: Session) -> uuid.UUID | None:
    inspector = inspect(postgres_session.bind)
    if not inspector.has_table("users", schema="auth"):
        return None
    return postgres_session.execute(text("SELECT id FROM auth.users LIMIT 1")).scalar_one_or_none()


def _jurisdiction(postgres_session: Session) -> Jurisdiction:
    market = postgres_session.execute(
        select(Market).where(Market.slug == "los_angeles")
    ).scalar_one_or_none()
    if market is None:
        market = Market(
            slug="los_angeles",
            name="Los Angeles",
            display_name="Los Angeles",
            state="CA",
        )
        postgres_session.add(market)
        postgres_session.flush()
    jurisdiction = Jurisdiction(
        slug=f"test_jurisdiction_{uuid.uuid4().hex[:8]}",
        name="Test Jurisdiction",
        display_name="Test Jurisdiction",
        state="CA",
        market=market,
    )
    postgres_session.add(jurisdiction)
    postgres_session.flush()
    return jurisdiction


def _source_registration(
    postgres_session: Session,
    jurisdiction: Jurisdiction,
    source_name: str,
) -> SourceRegistration:
    registration = SourceRegistration(
        jurisdiction_id=jurisdiction.id,
        source_name=source_name,
        source_class="gov",
        active=True,
    )
    postgres_session.add(registration)
    postgres_session.flush()
    return registration


def _ensure_coverage_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "costar_uploads",
        "jurisdictions",
        "markets",
        "scrape_jobs",
        "source_registrations",
        "source_runs",
    }
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply the latest migrations before running coverage tests: {missing}")
