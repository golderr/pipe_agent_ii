from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.models import (
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
    ReviewItem,
    ReviewItemType,
    SourceRun,
)


def test_persist_collected_records_creates_status_change_review_item(
    postgres_session: Session,
) -> None:
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

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="11010-10000-02451",
        raw_payload={"pcis_permit": "11010-10000-02451"},
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        identifiers={"permit_number": ["11010-10000-02451"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2013-01-02",
            "total_units": 260,
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 1
    assert result.matched_by_address == 1
    assert result.inserted_source_records == 1
    assert result.inserted_identifiers == 1
    assert result.status_change_review_items == 1

    source_run = postgres_session.execute(
        select(SourceRun).where(SourceRun.id == result.source_run_id)
    ).scalar_one()
    assert source_run.records_pulled == 1
    assert source_run.new_matches == 1
    assert source_run.updates_found == 1

    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == result.source_run_id,
            ReviewItem.project_id == project.id,
        )
    ).scalar_one()
    assert review_item.item_type == ReviewItemType.STATUS_CHANGE
    assert review_item.project_id == project.id
    assert review_item.payload["status_suggestion"]["suggested_status"] == "Approved"
    assert review_item.payload["status_suggestion"]["evidence_type"] == "building_permit_issued"
    assert review_item.payload["status_suggestion"]["rule_code"] == "building_permit_issued"
    assert review_item.payload["status_suggestion"]["proof_level"] == "supporting"
    assert review_item.payload["changes"] == [
        {
            "field": "total_units",
            "old_value": None,
            "new_value": 260,
            "priority": "medium",
        }
    ]

    identifier_rows = postgres_session.execute(
        select(ProjectIdentifier.value).where(ProjectIdentifier.project_id == project.id)
    ).scalars()
    assert list(identifier_rows) == ["11010-10000-02451"]

    source_record = postgres_session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.project_id == project.id,
            ProjectSourceRecord.source_name == "ladbs_permits",
            ProjectSourceRecord.source_record_id == "11010-10000-02451",
        )
    ).scalar_one()
    assert source_record.project_id == project.id
    assert source_record.source_record_id == "11010-10000-02451"


def test_persist_collected_records_creates_new_candidate_review_item(
    postgres_session: Session,
) -> None:
    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-20000-02593",
        raw_payload={"pcis_permit": "12010-20000-02593"},
        canonical_address="132 LAUREL AVENUE LOS ANGELES CA 90048",
        identifiers={"permit_number": ["12010-20000-02593"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2013-01-02",
            "total_units": 1,
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 0
    assert result.new_candidate_review_items == 1

    source_run = postgres_session.execute(
        select(SourceRun).where(SourceRun.id == result.source_run_id)
    ).scalar_one()
    assert source_run.new_candidates == 1

    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one()
    assert review_item.item_type == ReviewItemType.NEW_CANDIDATE
    assert review_item.project_id is None
