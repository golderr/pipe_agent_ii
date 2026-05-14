from __future__ import annotations

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.models import PipelineStatus, Project
from tcg_pipeline.matching.differ import diff_project_against_record


def test_diff_project_against_record_builds_status_suggestion_from_permit_evidence() -> None:
    project = Project(
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        raw_addresses=["7270 Manchester Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PENDING,
    )
    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="11010-10000-02451",
        raw_payload={},
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2013-01-02",
        },
    )

    diff_result = diff_project_against_record(project, raw_record)

    assert diff_result.status_suggestion is not None
    assert diff_result.status_suggestion.current_status == PipelineStatus.PENDING
    assert diff_result.status_suggestion.suggested_status == PipelineStatus.APPROVED
    assert diff_result.status_suggestion.evidence_type == "building_permit_issued"
    assert diff_result.status_suggestion.rule_code == "building_permit_issued"
    assert diff_result.status_suggestion.proof_level == "supporting"
    assert diff_result.field_changes == []


def test_diff_project_against_record_skips_backward_status_suggestion() -> None:
    project = Project(
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        raw_addresses=["7270 Manchester Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.UNDER_CONSTRUCTION,
    )
    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="11010-10000-02451",
        raw_payload={},
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
        },
    )

    diff_result = diff_project_against_record(project, raw_record)

    assert diff_result.status_suggestion is None
    assert diff_result.field_changes == []


def test_diff_project_against_record_detects_stories_change() -> None:
    project = Project(
        canonical_address="100 STORY AVENUE LOS ANGELES CA 90045",
        raw_addresses=["100 Story Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        stories=5,
    )
    raw_record = RawRecord(
        source_name="pipedream",
        source_record_id="pd-100",
        raw_payload={},
        canonical_address="100 STORY AVENUE LOS ANGELES CA 90045",
        mapped_fields={"stories": "7"},
    )

    diff_result = diff_project_against_record(project, raw_record)

    assert len(diff_result.field_changes) == 1
    change = diff_result.field_changes[0]
    assert change.field == "stories"
    assert change.old_value == 5
    assert change.new_value == 7
