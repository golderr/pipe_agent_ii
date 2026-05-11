from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.agents.profiles import PERMIT_AGENT_PROFILE
from tcg_pipeline.agents.runner import AgentClientResult, AgentRunRequest
from tcg_pipeline.cli import _resolve_incremental_cursor
from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    AgeRestriction,
    DeveloperAlias,
    DeveloperRegistry,
    Evidence,
    IdentifierType,
    PipelineStatus,
    ProductType,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
    ReviewItem,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC, LLMUsage
from tcg_pipeline.settings import Settings


class FakePermitAgentClient:
    provider = LLM_PROVIDER_ANTHROPIC
    model = DEFAULT_EXTRACTION_MODEL
    prompt_version = PERMIT_AGENT_PROFILE.prompt_version

    def __init__(self, verdict: dict[str, Any] | None = None) -> None:
        self.requests: list[AgentRunRequest] = []
        self.verdict = verdict or {"decision": "no_change"}

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        self.requests.append(request)
        return AgentClientResult(
            outcome=AgentRunOutcome.COMPLETED.value,
            usage=LLMUsage(
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=0,
                output_tokens=20,
            ),
            latency_ms=123,
            reasoning_trace="Permit attribution reviewed against project and permit context.",
            evidence_consulted=[
                {
                    "source_type": "ladbs_permit",
                    "record_id": request.intake.intake_record_id,
                    "role": "primary",
                }
            ],
            tool_calls_summary=[
                {
                    "tool": "get_permits_for_project",
                    "args_summary": "{}",
                    "result_summary": "permit history checked",
                    "latency_ms": 1,
                    "output_token_count": 20,
                }
            ],
            agent_revised_verdict=self.verdict,
        )


def _review_items_by_field(
    session: Session,
    *,
    source_run_id,
    project_id,
) -> dict[str, ReviewItem]:
    rows = (
        session.execute(
            select(ReviewItem).where(
                ReviewItem.source_run_id == source_run_id,
                ReviewItem.project_id == project_id,
            )
        )
        .scalars()
        .all()
    )
    return {str(row.field_name): row for row in rows}


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
        source_row_id="row-57hi~6iij-sky2",
        source_created_at=datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC),
        source_updated_at=datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC),
        source_row_hash="abc123",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        collection_mode="incremental",
        incremental_since=datetime(2020, 5, 3, 9, 18, 23, 851000, tzinfo=UTC),
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.collection_mode == "incremental"
    assert result.matched_existing_projects == 1
    assert result.matched_by_address == 1
    assert result.inserted_source_records == 1
    assert result.inserted_identifiers == 1
    assert result.status_change_review_items == 3
    assert result.source_min_updated_at == datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC)
    assert result.source_max_updated_at == datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC)

    source_run = postgres_session.execute(
        select(SourceRun).where(SourceRun.id == result.source_run_id)
    ).scalar_one()
    assert source_run.records_pulled == 1
    assert source_run.collection_mode == "incremental"
    assert source_run.incremental_since == datetime(2020, 5, 3, 9, 18, 23, 851000, tzinfo=UTC)
    assert source_run.source_min_updated_at == datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC)
    assert source_run.source_max_updated_at == datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC)
    assert source_run.new_matches == 1
    assert source_run.updates_found == 1

    review_items = _review_items_by_field(
        postgres_session,
        source_run_id=result.source_run_id,
        project_id=project.id,
    )
    assert set(review_items) == {"pipeline_status", "total_units", "date_delivery"}
    review_item = review_items["pipeline_status"]
    assert review_item.item_type == ReviewItemType.STATUS_CHANGE
    assert review_item.project_id == project.id
    assert review_item.field_name == "pipeline_status"
    assert len(review_item.payload["evidence_ids"]) == 1
    assert review_item.winning_evidence_id is not None
    assert review_item.payload["status_suggestion"]["suggested_status"] == "Approved"
    assert review_item.payload["status_suggestion"]["evidence_type"] == "building_permit_issued"
    assert review_item.payload["status_suggestion"]["rule_code"] == "building_permit_issued"
    assert review_item.payload["status_suggestion"]["proof_level"] == "supporting"
    assert review_item.payload["status_suggestion"]["reason"] == (
        "Permit issued alone supports Approved, but requires researcher review until "
        "corroborating construction evidence arrives."
    )
    assert review_item.payload["review_flags"] == [
        {
            "code": "permit_issued_requires_review",
            "message": (
                "Permit issued alone supports Approved, but requires researcher review "
                "until corroborating construction evidence arrives."
            ),
            "priority": "high",
        }
    ]
    assert review_item.payload["changes"] == []
    assert review_items["total_units"].payload["changes"] == [
        {
            "field": "total_units",
            "old_value": None,
            "new_value": 260,
            "priority": "medium",
        },
    ]
    assert review_items["date_delivery"].payload["changes"] == [
        {
            "field": "date_delivery",
            "old_value": None,
            "new_value": date(date.today().year + 3, 7, 1).isoformat(),
            "priority": "medium",
        },
    ]

    postgres_session.refresh(project)
    assert project.pipeline_status == PipelineStatus.APPROVED
    assert project.total_units == 260

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
    assert source_record.source_row_id == "row-57hi~6iij-sky2"
    assert source_record.source_created_at == datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC)
    assert source_record.source_updated_at == datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC)
    assert source_record.source_row_hash == "abc123"

    evidence_row = postgres_session.execute(
        select(Evidence).where(
            Evidence.project_id == project.id,
            Evidence.source_type == "ladbs_permit",
            Evidence.source_record_id == "11010-10000-02451",
        )
    ).scalar_one()
    assert evidence_row.evidence_date == date(2013, 1, 2)
    assert evidence_row.extracted_fields["total_units"] == {"value": 260, "confidence": None}


def test_persist_collected_records_skips_unchanged_overlap_rows(
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

    initial_raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="11010-10000-02451",
        raw_payload={
            ":id": "row-1",
            "as_of_date": date(2026, 4, 15),
            ":updated_at": "2026-04-15T12:00:00.000Z",
            "pcis_permit": "11010-10000-02451",
        },
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        identifiers={"permit_number": ["11010-10000-02451"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": date(2013, 1, 2),
            "total_units": 260,
        },
        source_row_id="row-1",
        source_created_at=datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC),
        source_updated_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        source_row_hash="stable-hash",
    )
    initial_result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[initial_raw_record],
        collection_mode="full",
    )
    postgres_session.flush()

    original_source_record = postgres_session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.project_id == project.id,
            ProjectSourceRecord.source_name == "ladbs_permits",
            ProjectSourceRecord.source_record_id == "11010-10000-02451",
        )
    ).scalar_one()
    original_seen_at = original_source_record.last_seen_at
    assert initial_result.inserted_source_records == 1

    postgres_session.expire_all()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="11010-10000-02451",
        raw_payload={
            ":id": "row-1",
            "as_of_date": date(2026, 4, 15),
            ":updated_at": "2026-04-16T12:00:00.000Z",
            "pcis_permit": "11010-10000-02451",
        },
        canonical_address="7270 MANCHESTER AVENUE LOS ANGELES CA 90045",
        identifiers={"permit_number": ["11010-10000-02451"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": date(2013, 1, 2),
            "total_units": 260,
        },
        source_row_id="row-1",
        source_created_at=datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC),
        source_updated_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        source_row_hash="stable-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        collection_mode="incremental",
        incremental_since=datetime(2026, 4, 15, 0, 0, tzinfo=UTC),
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 1
    assert result.matched_by_source_record == 1
    assert result.inserted_source_records == 0
    assert result.updated_source_records == 0
    assert result.unchanged_source_records == 1
    assert result.inserted_identifiers == 0
    assert result.status_change_review_items == 0

    source_run = postgres_session.execute(
        select(SourceRun).where(SourceRun.id == result.source_run_id)
    ).scalar_one()
    assert source_run.new_matches == 0
    assert source_run.updates_found == 0

    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one_or_none()
    assert review_item is None

    source_record = postgres_session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.project_id == project.id,
            ProjectSourceRecord.source_name == "ladbs_permits",
            ProjectSourceRecord.source_record_id == "11010-10000-02451",
        )
    ).scalar_one()
    assert source_record.source_updated_at == datetime(2026, 4, 16, 12, 0, tzinfo=UTC)
    assert source_record.source_row_hash == "stable-hash"
    assert source_record.raw_payload[":updated_at"] == "2026-04-16T12:00:00.000Z"
    assert source_record.raw_payload["as_of_date"] == "2026-04-15"
    assert source_record.mapped_fields == {
        "status_evidence_type": "building_permit_issued",
        "status_evidence_date": "2013-01-02",
        "total_units": 260,
    }
    assert source_record.last_seen_at is not None
    assert source_record.last_seen_at > original_seen_at

    evidence_rows = postgres_session.execute(
        select(Evidence).where(
            Evidence.project_id == project.id,
            Evidence.source_type == "ladbs_permit",
            Evidence.source_record_id == "11010-10000-02451",
        )
    ).scalars()
    assert len(list(evidence_rows)) == 1


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
    assert review_item.payload["status_suggestion"] == {
        "current_status": None,
        "suggested_status": "Approved",
        "evidence_type": "building_permit_issued",
        "evidence_date": "2013-01-02",
        "reason": (
            "Building permit issued. Per TCG status definitions, permit issuance supports "
            "Approved but does not prove Under Construction."
        ),
        "priority": "high",
        "rule_code": "building_permit_issued",
        "proof_level": "supporting",
    }


def test_persist_collected_records_routes_ladbs_new_candidate_to_agent(
    postgres_session: Session,
) -> None:
    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-20000-09999",
        raw_payload={"pcis_permit": "12010-20000-09999", "apn": "5146013024"},
        canonical_address="133 LAUREL AVENUE LOS ANGELES CA 90048",
        identifiers={"permit_number": ["12010-20000-09999"], "apn": ["5146013024"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-05-10",
            "total_units": 80,
            "apn": "5146013024",
        },
    )
    client = FakePermitAgentClient()

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        permit_agent_client=client,
        settings=Settings(agent_enabled_for_permits=True),
    )
    postgres_session.flush()

    assert result.new_candidate_review_items == 1
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.profile.name == "permit_v1"
    assert request.intake.source_type == "ladbs_permit"
    assert request.intake.intake_record_id == "12010-20000-09999"
    assert request.trigger_reasons == ("new_candidate",)
    assert request.intake.payload["mapped_fields"]["total_units"] == 80

    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one()
    assert agent_run.profile_name == "permit_v1"
    assert agent_run.triggered_by == ["new_candidate"]
    assert agent_run.intake_record_id == "12010-20000-09999"
    assert agent_run.project_id is None
    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one()
    link = postgres_session.execute(
        select(AgentRunReviewItem).where(
            AgentRunReviewItem.agent_run_id == agent_run.id,
            AgentRunReviewItem.review_item_id == review_item.id,
        )
    ).scalar_one()
    assert link.review_item_id == review_item.id


def test_persist_collected_records_can_suppress_new_candidate_review_items(
    postgres_session: Session,
) -> None:
    raw_record = RawRecord(
        source_name="ladbs_permit_activity",
        source_record_id="23016-90000-16465",
        raw_payload={"pcis_permit": "23016-90000-16465"},
        canonical_address="8317 DENISE LANE LOS ANGELES CA 91304",
        identifiers={"permit_number": ["23016-90000-16465"]},
        mapped_fields={
            "permit_issue_date": "2023-05-19",
            "permit_type": "Bldg-Alter/Repair",
            "total_units": 1,
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permit_activity",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 0
    assert result.new_candidate_review_items == 0
    assert result.suppressed_new_candidate_records == 1
    assert result.possible_match_review_items == 0

    source_run = postgres_session.execute(
        select(SourceRun).where(SourceRun.id == result.source_run_id)
    ).scalar_one()
    assert source_run.new_candidates == 0

    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one_or_none()
    assert review_item is None

    evidence_row = postgres_session.execute(
        select(Evidence).where(
            Evidence.project_id.is_(None),
            Evidence.source_type == "ladbs_permit",
            Evidence.source_record_id == "23016-90000-16465",
        )
    ).scalar_one()
    assert evidence_row.project_id is None


def test_persist_collected_records_keeps_possible_match_review_items_when_new_candidates_suppressed(
    postgres_session: Session,
) -> None:
    canonical_address = "8317 DENISE LANE LOS ANGELES CA 91304"
    postgres_session.add_all(
        [
            Project(
                canonical_address=canonical_address,
                raw_addresses=["8317 Denise Lane"],
                market="los_angeles",
                city="Los Angeles",
                state="CA",
                county="Los Angeles",
            ),
            Project(
                canonical_address=canonical_address,
                raw_addresses=["8317 Denise Lane"],
                market="los_angeles",
                city="Los Angeles",
                state="CA",
                county="Los Angeles",
            ),
        ]
    )
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permit_activity",
        source_record_id="23016-90000-16465",
        raw_payload={"pcis_permit": "23016-90000-16465"},
        canonical_address=canonical_address,
        mapped_fields={
            "permit_issue_date": "2023-05-19",
            "permit_type": "Bldg-Alter/Repair",
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permit_activity",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 0
    assert result.new_candidate_review_items == 0
    assert result.suppressed_new_candidate_records == 0
    assert result.possible_match_review_items == 1

    source_run = postgres_session.execute(
        select(SourceRun).where(SourceRun.id == result.source_run_id)
    ).scalar_one()
    assert source_run.new_candidates == 0

    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one()
    assert review_item.item_type == ReviewItemType.POSSIBLE_MATCH
    assert review_item.payload["status_suggestion"] is None


def test_persist_collected_records_matches_existing_project_when_new_candidates_suppressed(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="8317 DENISE LANE LOS ANGELES CA 91304",
        raw_addresses=["8317 Denise Lane"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permit_activity",
        source_record_id="23016-90000-16465",
        raw_payload={"pcis_permit": "23016-90000-16465"},
        canonical_address="8317 DENISE LANE LOS ANGELES CA 91304",
        identifiers={"permit_number": ["23016-90000-16465"]},
        mapped_fields={
            "permit_issue_date": "2023-05-19",
            "permit_type": "Bldg-Alter/Repair",
            "total_units": 5,
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permit_activity",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 1
    assert result.matched_by_address == 1
    assert result.inserted_source_records == 1
    assert result.inserted_identifiers == 1
    assert result.new_candidate_review_items == 0
    assert result.suppressed_new_candidate_records == 0
    assert result.status_change_review_items == 2

    source_record = postgres_session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.project_id == project.id,
            ProjectSourceRecord.source_name == "ladbs_permit_activity",
            ProjectSourceRecord.source_record_id == "23016-90000-16465",
        )
    ).scalar_one()
    assert source_record.project_id == project.id

    review_items = _review_items_by_field(
        postgres_session,
        source_run_id=result.source_run_id,
        project_id=project.id,
    )
    assert set(review_items) == {"total_units", "date_delivery"}
    review_item = review_items["total_units"]
    assert review_item.item_type == ReviewItemType.STATUS_CHANGE
    assert review_item.payload["status_suggestion"] is None
    assert review_item.payload["changes"] == [
        {
            "field": "total_units",
            "old_value": None,
            "new_value": 5,
            "priority": "medium",
        },
    ]
    assert review_items["date_delivery"].payload["changes"] == [
        {
            "field": "date_delivery",
            "old_value": None,
            "new_value": date(date.today().year + 5, 7, 1).isoformat(),
            "priority": "medium",
        },
    ]


def test_persist_collected_records_matches_existing_project_when_raw_address_omits_zip(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA 91324",
        raw_addresses=["9301 N Tampa Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        zip="91324",
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_inspections",
        source_record_id="row-jgem~t949~4xfc",
        raw_payload={"address": "9301 N TAMPA AVE"},
        canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA",
        mapped_fields={
            "inspection": "Frame Inspection",
            "inspection_date": "2026-04-11",
            "inspection_result": "Approved",
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_inspections",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 1
    assert result.matched_by_address == 1
    assert result.inserted_source_records == 1
    assert result.possible_match_review_items == 0

    source_record = postgres_session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.project_id == project.id,
            ProjectSourceRecord.source_name == "ladbs_inspections",
            ProjectSourceRecord.source_record_id == "row-jgem~t949~4xfc",
        )
    ).scalar_one()
    assert source_record.project_id == project.id


def test_persist_collected_records_flags_unit_split_mismatch_when_total_changes(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9500 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9500 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
        affordable_units=20,
        market_rate_units=80,
        workforce_units=0,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-02593",
        raw_payload={"pcis_permit": "12010-30000-02593"},
        canonical_address="9500 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-02593"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 120,
        },
        source_row_hash="split-mismatch-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.status_change_review_items == 2

    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == result.source_run_id,
            ReviewItem.project_id == project.id,
            ReviewItem.field_name == "total_units",
        )
    ).scalar_one()
    assert {
        "code": "unit_split_mismatch",
        "message": (
            "Total units updated to 120. Affordable/market-rate/workforce split "
            "(20/80/0) may need revision because the split no longer sums to total."
        ),
        "priority": "medium",
    } in review_item.payload["review_flags"]


def test_persist_collected_records_routes_ladbs_unit_delta_to_agent(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9501 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9501 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-09999",
        raw_payload={"pcis_permit": "12010-30000-09999"},
        canonical_address="9501 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-09999"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 112,
        },
        source_row_hash="unit-delta-agent-hash",
    )
    client = FakePermitAgentClient()

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
        permit_agent_client=client,
        settings=Settings(agent_enabled_for_permits=True),
    )
    postgres_session.flush()

    assert result.status_change_review_items >= 1
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.trigger_reasons == ("unit_delta",)
    assert request.intake.project_id == project.id
    assert request.intake.payload["mapped_fields"]["total_units"] == 112

    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one()
    assert agent_run.profile_name == "permit_v1"
    assert agent_run.project_id == project.id
    assert agent_run.triggered_by == ["unit_delta"]
    linked_review_item_ids = set(
        postgres_session.execute(
            select(AgentRunReviewItem.review_item_id).where(
                AgentRunReviewItem.agent_run_id == agent_run.id
            )
        ).scalars()
    )
    total_units_review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == result.source_run_id,
            ReviewItem.project_id == project.id,
            ReviewItem.field_name == "total_units",
        )
    ).scalar_one()
    assert total_units_review_item.id in linked_review_item_ids


def test_persist_collected_records_routes_ladbs_product_type_change_to_agent(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9503 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9503 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
        product_type=ProductType.APARTMENT,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-10001",
        raw_payload={"pcis_permit": "12010-30000-10001"},
        canonical_address="9503 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-10001"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 100,
            "product_type": "Condo",
        },
        source_row_hash="product-type-agent-hash",
    )
    client = FakePermitAgentClient()

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
        permit_agent_client=client,
        settings=Settings(agent_enabled_for_permits=True),
    )
    postgres_session.flush()

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.trigger_reasons == ("product_type_change",)
    assert request.intake.project_id == project.id
    assert request.intake.payload["mapped_fields"]["product_type"] == "Condo"

    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one()
    assert agent_run.profile_name == "permit_v1"
    assert agent_run.project_id == project.id
    assert agent_run.triggered_by == ["product_type_change"]


def test_persist_collected_records_routes_combined_ladbs_triggers_to_one_agent_run(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9504 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9504 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
        product_type=ProductType.APARTMENT,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-10002",
        raw_payload={"pcis_permit": "12010-30000-10002"},
        canonical_address="9504 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-10002"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 112,
            "product_type": "Condo",
        },
        source_row_hash="combined-agent-hash",
    )
    client = FakePermitAgentClient()

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
        permit_agent_client=client,
        settings=Settings(agent_enabled_for_permits=True),
    )
    postgres_session.flush()

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.trigger_reasons == ("unit_delta", "product_type_change")

    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one()
    assert agent_run.triggered_by == ["unit_delta", "product_type_change"]


def test_persist_collected_records_writes_permit_audit_row_without_live_llm_client(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9502 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9502 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-10000",
        raw_payload={"pcis_permit": "12010-30000-10000"},
        canonical_address="9502 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-10000"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 112,
        },
        source_row_hash="small-unit-delta-agent-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
        settings=Settings(agent_enabled_for_permits=True, agent_allow_live_llm=False),
    )
    postgres_session.flush()

    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one()
    assert agent_run.outcome == AgentRunOutcome.KILLED_BY_SWITCH.value
    assert agent_run.error_text == (
        "agent_allow_live_llm=false; no AgentClient was provided for profile permit_v1"
    )
    assert agent_run.triggered_by == ["unit_delta"]
    assert agent_run.project_id == project.id


def test_persist_collected_records_writes_permit_kill_switch_audit_row(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9505 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9505 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-10003",
        raw_payload={"pcis_permit": "12010-30000-10003"},
        canonical_address="9505 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-10003"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 112,
        },
        source_row_hash="kill-switch-agent-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
        settings=Settings(agent_enabled_for_permits=False),
    )
    postgres_session.flush()

    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one()
    assert agent_run.outcome == AgentRunOutcome.KILLED_BY_SWITCH.value
    assert agent_run.error_text == "agent_enabled_for_permits=false"
    assert agent_run.triggered_by == ["unit_delta"]


def test_persist_collected_records_does_not_route_exact_ten_percent_unit_delta(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9506 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9506 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="12010-30000-10004",
        raw_payload={"pcis_permit": "12010-30000-10004"},
        canonical_address="9506 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"permit_number": ["12010-30000-10004"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-02",
            "total_units": 110,
        },
        source_row_hash="ten-percent-agent-hash",
    )
    client = FakePermitAgentClient()

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=False,
        permit_agent_client=client,
        settings=Settings(agent_enabled_for_permits=True),
    )
    postgres_session.flush()

    assert client.requests == []
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one_or_none()
    assert agent_run is None


def test_persist_collected_records_does_not_route_non_permit_sources_to_permit_agent(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="9507 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        raw_addresses=["9507 W Example Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="costar",
        source_record_id="costar-non-permit-10005",
        raw_payload={"costar_property_id": "costar-non-permit-10005"},
        canonical_address="9507 WEST EXAMPLE AVENUE LOS ANGELES CA 90035",
        identifiers={"costar_property_id": ["costar-non-permit-10005"]},
        mapped_fields={"total_units": 112},
        source_row_hash="non-permit-agent-hash",
    )
    client = FakePermitAgentClient()

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="costar",
        raw_records=[raw_record],
        create_new_candidates=False,
        permit_agent_client=client,
        settings=Settings(agent_enabled_for_permits=True),
    )
    postgres_session.flush()

    assert client.requests == []
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.source_run_id == result.source_run_id)
    ).scalar_one_or_none()
    assert agent_run is None


def test_persist_collected_records_reviews_resolved_developer_change(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="8800 WEST EXAMPLE BOULEVARD LOS ANGELES CA 90036",
        raw_addresses=["8800 W Example Blvd"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer="Old Dev",
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="costar",
        source_record_id="CST-REVIEW-DEV-1",
        raw_payload={"PropertyID": "CST-REVIEW-DEV-1"},
        canonical_address="8800 WEST EXAMPLE BOULEVARD LOS ANGELES CA 90036",
        mapped_fields={"developer": "New Dev"},
        source_row_hash="developer-review-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="costar",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    review_items = _review_items_by_field(
        postgres_session,
        source_run_id=result.source_run_id,
        project_id=project.id,
    )
    assert result.status_change_review_items == len(review_items)
    assert "developer" in review_items
    review_item = review_items["developer"]
    assert {
        "field": "developer",
        "old_value": "Old Dev",
        "new_value": "New Dev",
        "priority": "medium",
    } in review_item.payload["changes"]


def test_persist_collected_records_flags_fuzzy_developer_review_without_field_delta(
    postgres_session: Session,
) -> None:
    canonical_name = "ZZZQXQ Cimmer Group"
    raw_name = "ZZZQXQ C1mmer Grp"
    postgres_session.add(DeveloperRegistry(canonical_name=canonical_name))
    project = Project(
        canonical_address="8801 WEST EXAMPLE BOULEVARD LOS ANGELES CA 90036",
        raw_addresses=["8801 W Example Blvd"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer=canonical_name,
        date_delivery=date(date.today().year + 6, 7, 1),
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="costar",
        source_record_id="CST-REVIEW-DEV-2",
        raw_payload={"PropertyID": "CST-REVIEW-DEV-2"},
        canonical_address="8801 WEST EXAMPLE BOULEVARD LOS ANGELES CA 90036",
        mapped_fields={"developer": raw_name},
        source_row_hash="developer-review-fuzzy-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="costar",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    alias_rows = (
        postgres_session.execute(
            select(DeveloperAlias.alias_name).where(DeveloperAlias.alias_name == raw_name)
        )
        .scalars()
        .all()
    )
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == result.source_run_id,
            ReviewItem.project_id == project.id,
            ReviewItem.field_name == "developer",
        )
    ).scalar_one()

    assert project.developer == canonical_name
    assert result.status_change_review_items == 1
    assert review_item.payload["changes"] == []
    matching_flag = next(
        (
            review_flag
            for review_flag in review_item.payload["review_flags"]
            if review_flag["code"] == "developer_canonicalization_review"
        ),
        None,
    )
    assert matching_flag is not None
    assert matching_flag["priority"] == "medium"
    assert raw_name in matching_flag["message"]
    assert canonical_name in matching_flag["message"]
    assert alias_rows == []


def test_persist_collected_records_does_not_clear_fields_when_partial_evidence_arrives(
    postgres_session: Session,
) -> None:
    apn = "partial-evidence-apn-001"
    project = Project(
        canonical_address="111 TEST AVENUE LOS ANGELES CA 90001",
        raw_addresses=["111 Test Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.COMPLETE,
        product_type=ProductType.APARTMENT,
        age_restriction=AgeRestriction.NON_AGE_RESTRICTED,
        developer="Jamison Services",
        date_delivery=date(2024, 9, 15),
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        ProjectIdentifier(
            project_id=project.id,
            identifier_type=IdentifierType.APN,
            value=apn,
        )
    )
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="20010-10000-01382",
        raw_payload={"pcis_permit": "20010-10000-01382"},
        canonical_address="111 TEST AVENUE LOS ANGELES CA 90001",
        identifiers={"apn": [apn], "permit_number": ["20010-10000-01382"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2021-09-22",
        },
        source_row_hash="partial-permit-hash",
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        collection_mode="preview",
    )
    postgres_session.flush()

    postgres_session.refresh(project)
    assert result.matched_existing_projects == 1
    assert result.matched_by_identifier == 1
    assert result.status_change_review_items == 0
    assert project.pipeline_status == PipelineStatus.COMPLETE
    assert project.product_type == ProductType.APARTMENT
    assert project.age_restriction == AgeRestriction.NON_AGE_RESTRICTED
    assert project.developer == "Jamison Services"
    assert project.date_delivery == date(2024, 9, 15)

    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one_or_none()
    assert review_item is None


def test_persist_collected_records_keeps_zipless_address_matches_ambiguous(
    postgres_session: Session,
) -> None:
    postgres_session.add_all(
        [
            Project(
                canonical_address="100 SOUTH MAIN STREET LOS ANGELES CA 90012",
                raw_addresses=["100 S Main St"],
                market="los_angeles",
                city="Los Angeles",
                state="CA",
                county="Los Angeles",
                zip="90012",
            ),
            Project(
                canonical_address="100 SOUTH MAIN STREET LOS ANGELES CA 90013",
                raw_addresses=["100 S Main St"],
                market="los_angeles",
                city="Los Angeles",
                state="CA",
                county="Los Angeles",
                zip="90013",
            ),
        ]
    )
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_inspections",
        source_record_id="row-ambiguous",
        raw_payload={"address": "100 S MAIN ST"},
        canonical_address="100 SOUTH MAIN STREET LOS ANGELES CA",
        mapped_fields={
            "inspection": "Final",
            "inspection_date": "2026-04-11",
            "inspection_result": "Approved",
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_inspections",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.records_pulled == 1
    assert result.matched_existing_projects == 0
    assert result.possible_match_review_items == 1
    assert result.suppressed_new_candidate_records == 0

    review_item = postgres_session.execute(
        select(ReviewItem).where(ReviewItem.source_run_id == result.source_run_id)
    ).scalar_one()
    assert review_item.item_type == ReviewItemType.POSSIBLE_MATCH
    assert len(review_item.payload["match"]["candidate_project_ids"]) == 2


def test_persist_collected_records_skips_identifier_insert_when_value_belongs_to_other_project(
    postgres_session: Session,
) -> None:
    permit_number = "TEST-23043-10000-03939"
    existing_identifier_project = Project(
        canonical_address="111 WEST 1ST STREET LOS ANGELES CA 90012",
        raw_addresses=["111 W 1st St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
    matched_project = Project(
        canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA 91324",
        raw_addresses=["9301 N Tampa Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        zip="91324",
    )
    postgres_session.add_all([existing_identifier_project, matched_project])
    postgres_session.flush()
    postgres_session.add(
        ProjectIdentifier(
            project_id=existing_identifier_project.id,
            identifier_type=IdentifierType.PERMIT_NUMBER,
            value=permit_number,
        )
    )
    postgres_session.flush()

    raw_record = RawRecord(
        source_name="ladbs_inspections",
        source_record_id="row-conflict",
        raw_payload={"address": "9301 N TAMPA AVE"},
        canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA",
        identifiers={"permit_number": [permit_number]},
        mapped_fields={
            "inspection": "Frame Inspection",
            "inspection_date": "2026-04-11",
            "inspection_result": "Approved",
        },
    )

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_inspections",
        raw_records=[raw_record],
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.matched_existing_projects == 1
    assert result.inserted_source_records == 1
    assert result.inserted_identifiers == 0

    identifier_rows = postgres_session.execute(
        select(ProjectIdentifier.project_id).where(
            ProjectIdentifier.identifier_type == IdentifierType.PERMIT_NUMBER,
            ProjectIdentifier.value == permit_number,
        )
    ).scalars()
    assert list(identifier_rows) == [existing_identifier_project.id]


def test_persist_collected_records_inserts_permit_identifier_once_across_multiple_rows_in_same_run(
    postgres_session: Session,
) -> None:
    permit_number = "TEST-23043-10000-03939"
    project = Project(
        canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA 91324",
        raw_addresses=["9301 N Tampa Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        zip="91324",
    )
    postgres_session.add(project)
    postgres_session.flush()

    raw_records = [
        RawRecord(
            source_name="ladbs_inspections",
            source_record_id="row-1",
            raw_payload={"address": "9301 N TAMPA AVE"},
            canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA",
            identifiers={"permit_number": [permit_number]},
            mapped_fields={
                "inspection": "Frame Inspection",
                "inspection_date": "2026-04-11",
                "inspection_result": "Approved",
            },
        ),
        RawRecord(
            source_name="ladbs_inspections",
            source_record_id="row-2",
            raw_payload={"address": "9301 N TAMPA AVE"},
            canonical_address="9301 NORTH TAMPA AVENUE LOS ANGELES CA",
            identifiers={"permit_number": [permit_number]},
            mapped_fields={
                "inspection": "Final",
                "inspection_date": "2026-04-12",
                "inspection_result": "Approved",
            },
        ),
    ]

    result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_inspections",
        raw_records=raw_records,
        create_new_candidates=False,
    )
    postgres_session.flush()

    assert result.matched_existing_projects == 2
    assert result.inserted_source_records == 2
    assert result.inserted_identifiers == 1

    identifier_rows = postgres_session.execute(
        select(ProjectIdentifier.value).where(
            ProjectIdentifier.project_id == project.id,
            ProjectIdentifier.identifier_type == IdentifierType.PERMIT_NUMBER,
        )
    ).scalars()
    assert list(identifier_rows) == [permit_number]


def test_resolve_incremental_cursor_uses_max_source_updated_at(
    postgres_session: Session,
) -> None:
    market = "cursor_test_market"
    source_name = "ladbs_permits_cursor_test"
    postgres_session.add_all(
        [
            SourceRun(
                market=market,
                source_name=source_name,
                run_timestamp=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
                source_max_updated_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            ),
            SourceRun(
                market=market,
                source_name=source_name,
                run_timestamp=datetime(2026, 4, 16, 10, 0, tzinfo=UTC),
                source_max_updated_at=datetime(2026, 4, 16, 9, 0, tzinfo=UTC),
            ),
        ]
    )
    postgres_session.flush()

    cursor = _resolve_incremental_cursor(
        postgres_session,
        market=market,
        source_name=source_name,
        overlap_hours=24,
    )

    assert cursor == datetime(2026, 4, 15, 12, 0, tzinfo=UTC)


def test_resolve_incremental_cursor_returns_none_without_source_metadata(
    postgres_session: Session,
) -> None:
    cursor = _resolve_incremental_cursor(
        postgres_session,
        market="los_angeles",
        source_name="source_without_metadata",
        overlap_hours=24,
    )

    assert cursor is None
