from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from scripts.backfill_evidence import (
    BackfillEvidenceResult,
    _backfill_pipedream_snapshots,
    _backfill_source_record_evidence,
    _derive_source_record_evidence_date,
)
from tcg_pipeline.db.models import (
    Evidence,
    IdentifierType,
    PipelineStatus,
    ProductType,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
)
from tcg_pipeline.ingesters.pipedream import PIPEDREAM_CREATED_BY


def test_backfill_evidence_is_rerunnable_without_duplicate_rows(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running backfill persistence tests.")

    source_project = Project(
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        raw_addresses=["7270 Manchester Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
    pipedream_project = Project(
        canonical_address="9904 E EXAMPLE LANE LOS ANGELES CA 90001",
        raw_addresses=["9904 E Example Ln"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Example Homes",
        pipeline_status=PipelineStatus.PENDING,
        product_type=ProductType.APARTMENT,
        total_units=120,
        created_by=PIPEDREAM_CREATED_BY,
        last_edit_date=date(2026, 4, 15),
    )
    postgres_session.add_all([source_project, pipedream_project])
    postgres_session.flush()

    postgres_session.add(
        ProjectSourceRecord(
            project_id=source_project.id,
            source_name="ladbs_permits",
            source_record_id="11010-10000-02451",
            source_created_at=datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC),
            source_updated_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
            source_row_hash="abc123",
            first_seen_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
            last_seen_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
            last_pulled_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
            raw_payload={"pcis_permit": "11010-10000-02451"},
            mapped_fields={
                "status_evidence_type": "building_permit_issued",
                "status_evidence_date": "2013-01-02",
                "total_units": 260,
            },
        )
    )
    postgres_session.add(
        ProjectIdentifier(
            project_id=pipedream_project.id,
            identifier_type=IdentifierType.TCG_PIPEDREAM_ID,
            value="994.00001",
            source="pipedream",
            is_primary=True,
        )
    )
    postgres_session.add(
        ProjectSourceRecord(
            project_id=pipedream_project.id,
            source_name="pipedream",
            source_record_id="994.00001",
            first_seen_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            last_pulled_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            raw_payload={"ProjectID": "994.00001", "Name": "Example Homes"},
            mapped_fields={
                "project_name": "Example Homes",
                "pipeline_status": "Pending",
                "total_units": 120,
            },
        )
    )
    postgres_session.flush()

    result = BackfillEvidenceResult()
    result = _backfill_source_record_evidence(postgres_session, result=result)
    result = _backfill_pipedream_snapshots(postgres_session, result=result)
    postgres_session.flush()

    evidence_rows = postgres_session.execute(
        select(Evidence)
        .where(Evidence.project_id.in_([source_project.id, pipedream_project.id]))
        .order_by(Evidence.source_type, Evidence.source_record_id)
    )
    evidence_rows = evidence_rows.scalars().all()
    assert [row.source_type for row in evidence_rows] == ["ladbs_permit", "pipedream"]
    assert [row.source_record_id for row in evidence_rows] == ["11010-10000-02451", "994.00001"]
    assert result.inserted_source_record_rows >= 1
    assert result.inserted_pipedream_snapshots >= 1

    postgres_session.expire_all()

    rerun_result = BackfillEvidenceResult()
    rerun_result = _backfill_source_record_evidence(postgres_session, result=rerun_result)
    rerun_result = _backfill_pipedream_snapshots(postgres_session, result=rerun_result)
    postgres_session.flush()

    rerun_rows = postgres_session.execute(
        select(Evidence)
        .where(Evidence.project_id.in_([source_project.id, pipedream_project.id]))
        .order_by(Evidence.source_type, Evidence.source_record_id)
    )
    rerun_rows = rerun_rows.scalars().all()
    assert len(rerun_rows) == 2
    assert rerun_result.inserted_source_record_rows == 0
    assert rerun_result.inserted_pipedream_snapshots == 0


def test_backfill_source_record_evidence_skips_preexisting_orphan_duplicate(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running backfill persistence tests.")

    project = Project(
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        raw_addresses=["7270 Manchester Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
    postgres_session.add(project)
    postgres_session.flush()

    source_record = ProjectSourceRecord(
        project_id=project.id,
        source_name="ladbs_permits",
        source_record_id="11010-10000-02451",
        source_created_at=datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC),
        source_updated_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
        source_row_hash="abc123",
        first_seen_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
        last_seen_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
        last_pulled_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
        raw_payload={"pcis_permit": "11010-10000-02451"},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2013-01-02",
            "total_units": 260,
        },
    )
    postgres_session.add(source_record)
    postgres_session.flush()

    postgres_session.add(
        Evidence(
            project_id=None,
            source_type="ladbs_permit",
            source_tier=1,
            ingest_method="scheduled_collector",
            source_record_id="11010-10000-02451",
            collected_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
            evidence_date=date(2013, 1, 2),
            raw_data={"pcis_permit": "11010-10000-02451"},
            raw_data_hash="abc123",
            extracted_fields={
                "status_evidence_type": {
                    "value": "building_permit_issued",
                    "confidence": None,
                },
                "status_evidence_date": {"value": "2013-01-02", "confidence": None},
                "total_units": {"value": 260, "confidence": None},
            },
        )
    )
    postgres_session.flush()

    result = _backfill_source_record_evidence(postgres_session, result=BackfillEvidenceResult())
    postgres_session.flush()

    evidence_rows = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == "11010-10000-02451")
    ).scalars().all()
    assert len(evidence_rows) == 1
    assert result.skipped_duplicates >= 1


def test_source_record_evidence_date_ignores_future_delivery_projection() -> None:
    source_record = ProjectSourceRecord(
        source_name="costar",
        source_record_id="costar-123",
        first_seen_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        last_pulled_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        mapped_fields={
            "pipeline_status": "Proposed",
            "date_delivery": "2029-01-01",
            "developer": "Example Developer",
        },
    )

    assert _derive_source_record_evidence_date(source_record) == date(2026, 4, 16)
