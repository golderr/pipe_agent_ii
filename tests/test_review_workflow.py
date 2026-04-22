from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.models import (
    ChangeLog,
    DismissedRecord,
    DismissReason,
    Evidence,
    IdentifierType,
    PipelineStatus,
    Priority,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.db.review_workflow import (
    accept_review_item,
    defer_review_item,
    reject_review_item,
)


def _ensure_review_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "evidence",
        "review_items",
        "review_decisions",
        "change_log",
        "dismissed_records",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply the latest migrations before running review workflow tests: {missing}")


def _build_project(canonical_address: str, **overrides) -> Project:
    defaults = {
        "raw_addresses": [canonical_address],
        "market": "los_angeles",
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
    }
    defaults.update(overrides)
    return Project(canonical_address=canonical_address, **defaults)


def _add_discovery_review_item(
    postgres_session: Session,
    *,
    source_name: str = "ladbs_permits",
    source_record_id: str = "review-source-1",
    item_type: ReviewItemType = ReviewItemType.NEW_CANDIDATE,
    candidate_project_ids: list[uuid.UUID] | None = None,
    mapped_fields: dict[str, object] | None = None,
    identifiers: dict[str, list[str]] | None = None,
    canonical_address: str = "123 REVIEW STREET LOS ANGELES CA 90012",
) -> tuple[SourceRun, ReviewItem]:
    source_run = SourceRun(
        market="los_angeles",
        source_name=source_name,
        collection_mode="incremental",
    )
    postgres_session.add(source_run)
    postgres_session.flush()

    payload = {
        "match": {
            "match_type": None,
            "confidence": None,
            "candidate_project_ids": [
                str(project_id) for project_id in (candidate_project_ids or [])
            ],
            "matched_identifier_type": None,
            "matched_identifier_value": None,
        },
        "source_record_id": source_record_id,
        "canonical_address": canonical_address,
        "identifiers": identifiers or {},
        "mapped_fields": mapped_fields
        or {
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-04-01",
            "total_units": 120,
        },
        "status_suggestion": {
            "current_status": None,
            "suggested_status": "Approved",
            "evidence_type": "building_permit_issued",
            "evidence_date": "2026-04-01",
            "reason": "Permit issued.",
            "priority": "high",
            "rule_code": "building_permit_issued",
            "proof_level": "supporting",
        },
        "raw_payload": {
            ":id": "row-review-1",
            ":updated_at": "2026-04-15T12:00:00Z",
            "pcis_permit": source_record_id,
        },
        "source_row_id": "row-review-1",
        "source_updated_at": "2026-04-15T12:00:00Z",
        "source_row_hash": "workflow-source-row-hash",
    }
    review_item = ReviewItem(
        source_run_id=source_run.id,
        item_type=item_type,
        status=ReviewItemStatus.OPEN,
        priority=Priority.MEDIUM,
        payload=payload,
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    return source_run, review_item


def _add_orphan_evidence(
    postgres_session: Session,
    *,
    source_type: str = "ladbs_permit",
    source_record_id: str = "review-source-1",
    count: int = 2,
    mapped_fields: dict[str, object] | None = None,
) -> list[Evidence]:
    evidence_rows: list[Evidence] = []
    for index in range(count):
        evidence = Evidence(
            project_id=None,
            source_type=source_type,
            source_tier=2,
            ingest_method="scheduled_collector",
            source_record_id=source_record_id,
            collected_at=datetime(2026, 4, 15, 12, index, tzinfo=UTC),
            evidence_date=date(2026, 4, 1),
            raw_data={"pcis_permit": source_record_id, "version": index},
            raw_data_hash=f"workflow-evidence-hash-{index}",
            extracted_fields={
                key: {"value": value, "confidence": None}
                for key, value in (
                    mapped_fields
                    or {
                        "status_evidence_type": "building_permit_issued",
                        "status_evidence_date": "2026-04-01",
                        "total_units": 120,
                    }
                ).items()
            },
        )
        postgres_session.add(evidence)
        evidence_rows.append(evidence)
    postgres_session.flush()
    return evidence_rows


def test_accept_review_item_links_all_orphan_evidence_and_upserts_source_record(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    project = _build_project(
        "700 ACCEPTANCE BOULEVARD LOS ANGELES CA 90012",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add(project)
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-accept-1")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-accept-1",
        identifiers={"permit_number": ["permit-accept-1"]},
    )

    result = accept_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        project_id=project.id,
        notes="Reviewed and accepted.",
    )
    postgres_session.flush()
    postgres_session.refresh(project)
    postgres_session.refresh(review_item)

    linked_rows = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == "permit-accept-1")
    ).scalars().all()
    source_record = postgres_session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.source_name == "ladbs_permits",
            ProjectSourceRecord.source_record_id == "permit-accept-1",
        )
    ).scalar_one()
    follow_up_review_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == review_item.source_run_id,
            ReviewItem.id != review_item.id,
        )
    ).scalars().all()
    review_decision = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.review_item_id == review_item.id)
    ).scalar_one()
    identifier = postgres_session.execute(
        select(ProjectIdentifier).where(
            ProjectIdentifier.project_id == project.id,
            ProjectIdentifier.identifier_type == IdentifierType.PERMIT_NUMBER,
            ProjectIdentifier.value == "permit-accept-1",
        )
    ).scalar_one()
    change_log_fields = postgres_session.execute(
        select(ChangeLog.field).where(ChangeLog.review_item_id == review_item.id)
    ).scalars().all()

    assert result.project_id == project.id
    assert result.linked_evidence_count == 2
    assert result.source_record_created is True
    assert result.source_record_updated is False
    assert result.identifiers_inserted == 1
    assert result.follow_up_review_items_created == 1
    assert result.identifier_conflicts == []
    assert review_item.status == ReviewItemStatus.ACCEPTED
    assert review_item.project_id == project.id
    assert review_decision.action == ReviewDecisionAction.ACCEPT
    assert identifier.project_id == project.id
    assert all(evidence.project_id == project.id for evidence in linked_rows)
    assert source_record.project_id == project.id
    assert len(follow_up_review_items) == 1
    assert follow_up_review_items[0].item_type == ReviewItemType.STATUS_CHANGE
    assert follow_up_review_items[0].status == ReviewItemStatus.OPEN
    assert follow_up_review_items[0].payload["origin"] == "post_accept_resolution"
    assert {
        "code": "permit_issued_requires_review",
        "message": (
            "Permit issued alone supports Approved, but requires researcher review "
            "until corroborating construction evidence arrives."
        ),
        "priority": "high",
    } in follow_up_review_items[0].payload["review_flags"]
    assert project.pipeline_status == PipelineStatus.APPROVED
    assert project.total_units == 120
    assert "pipeline_status" in change_log_fields
    assert "total_units" in change_log_fields
    assert "date_delivery" in change_log_fields


def test_accept_review_item_merges_field_overrides_before_resolve(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    project = _build_project("701 OVERRIDE WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-override-1")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-override-1",
    )

    accept_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        project_id=project.id,
        field_overrides={"total_units": 300},
        notes="Researcher confirmed 300 units.",
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    assert project.total_units == 300
    assert project.researcher_override["total_units"]["value"] == 300
    assert project.researcher_override["total_units"]["set_by"] == "nate"
    assert project.researcher_override["total_units"]["note"] == "Researcher confirmed 300 units."


def test_accept_review_item_raises_when_matching_evidence_belongs_to_another_project(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    target_project = _build_project("705 TARGET WAY LOS ANGELES CA 90012")
    foreign_project = _build_project("706 FOREIGN WAY LOS ANGELES CA 90012")
    postgres_session.add_all([target_project, foreign_project])
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-conflict-1", count=1)
    postgres_session.add(
        Evidence(
            project_id=foreign_project.id,
            source_type="ladbs_permit",
            source_tier=2,
            ingest_method="scheduled_collector",
            source_record_id="permit-conflict-1",
            collected_at=datetime(2026, 4, 15, 12, 1, tzinfo=UTC),
            evidence_date=date(2026, 4, 2),
            raw_data={"pcis_permit": "permit-conflict-1", "version": "foreign"},
            raw_data_hash="workflow-foreign-evidence-hash",
            extracted_fields={
                "total_units": {"value": 55, "confidence": None},
            },
        )
    )
    postgres_session.flush()
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-conflict-1",
    )

    with pytest.raises(ValueError, match="already linked to other project"):
        accept_review_item(
            postgres_session,
            review_item_id=review_item.id,
            actor="nate",
            project_id=target_project.id,
        )

    orphan_rows = postgres_session.execute(
        select(Evidence).where(
            Evidence.project_id.is_(None),
            Evidence.source_record_id == "permit-conflict-1",
        )
    ).scalars().all()
    assert len(orphan_rows) == 1


def test_accept_review_item_raises_when_source_record_belongs_to_another_project(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    target_project = _build_project("707 TARGET PSR WAY LOS ANGELES CA 90012")
    foreign_project = _build_project("708 FOREIGN PSR WAY LOS ANGELES CA 90012")
    postgres_session.add_all([target_project, foreign_project])
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-psr-conflict-1", count=1)
    postgres_session.add(
        ProjectSourceRecord(
            project_id=foreign_project.id,
            source_name="ladbs_permits",
            source_record_id="permit-psr-conflict-1",
        )
    )
    postgres_session.flush()
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-psr-conflict-1",
    )

    with pytest.raises(ValueError, match="source record ladbs_permits:permit-psr-conflict-1"):
        accept_review_item(
            postgres_session,
            review_item_id=review_item.id,
            actor="nate",
            project_id=target_project.id,
        )


def test_accept_review_item_can_create_new_project_and_validate_possible_match_candidates(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    candidate = _build_project("702 CANDIDATE LANE LOS ANGELES CA 90012")
    wrong_candidate = _build_project("703 WRONG LANE LOS ANGELES CA 90012")
    postgres_session.add_all([candidate, wrong_candidate])
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-create-new-1")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-create-new-1",
        item_type=ReviewItemType.POSSIBLE_MATCH,
        candidate_project_ids=[candidate.id],
    )

    with pytest.raises(ValueError):
        accept_review_item(
            postgres_session,
            review_item_id=review_item.id,
            actor="nate",
            project_id=wrong_candidate.id,
        )

    result = accept_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        create_new=True,
        new_project_data={
            "city": "Los Angeles",
            "state": "CA",
            "county": "Los Angeles",
        },
    )
    postgres_session.flush()

    new_project = postgres_session.get(Project, result.project_id)
    assert new_project is not None
    assert new_project.canonical_address == "123 REVIEW STREET LOS ANGELES CA 90012"
    assert new_project.market == "los_angeles"
    assert new_project.pipeline_status == PipelineStatus.APPROVED
    assert new_project.total_units == 120


def test_accept_review_item_surfaces_identifier_conflicts_without_failing_accept(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    existing_identifier_project = _build_project("709 EXISTING IDENTIFIER WAY LOS ANGELES CA 90012")
    accepted_project = _build_project("710 ACCEPTED IDENTIFIER WAY LOS ANGELES CA 90012")
    postgres_session.add_all([existing_identifier_project, accepted_project])
    postgres_session.flush()
    postgres_session.add(
        ProjectIdentifier(
            project_id=existing_identifier_project.id,
            identifier_type=IdentifierType.PERMIT_NUMBER,
            value="permit-identifier-conflict-1",
        )
    )
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-identifier-conflict-1")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-identifier-conflict-1",
        identifiers={"permit_number": ["permit-identifier-conflict-1"]},
    )

    result = accept_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        project_id=accepted_project.id,
    )
    postgres_session.flush()

    accepted_identifiers = postgres_session.execute(
        select(ProjectIdentifier).where(
            ProjectIdentifier.project_id == accepted_project.id,
            ProjectIdentifier.identifier_type == IdentifierType.PERMIT_NUMBER,
        )
    ).scalars().all()

    assert result.identifiers_inserted == 0
    assert len(result.identifier_conflicts) == 1
    assert result.identifier_conflicts[0].identifier_type == IdentifierType.PERMIT_NUMBER
    assert result.identifier_conflicts[0].value == "permit-identifier-conflict-1"
    assert result.identifier_conflicts[0].owner_project_id == existing_identifier_project.id
    assert accepted_identifiers == []


def test_accept_review_item_is_not_repeatable_once_resolved(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    project = _build_project("704 IDEMPOTENT DRIVE LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()

    _add_orphan_evidence(postgres_session, source_record_id="permit-idempotent-1")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-idempotent-1",
    )

    accept_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        project_id=project.id,
    )
    postgres_session.flush()

    with pytest.raises(ValueError):
        accept_review_item(
            postgres_session,
            review_item_id=review_item.id,
            actor="nate",
            project_id=project.id,
        )


def test_reject_review_item_creates_dismissed_record_and_suppresses_future_review_items(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-dismiss-1",
    )

    result = reject_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        notes="Not a live project.",
        reason=DismissReason.OTHER,
    )
    postgres_session.flush()
    postgres_session.refresh(review_item)

    dismissed_record = postgres_session.execute(
        select(DismissedRecord).where(
            DismissedRecord.source == "ladbs_permits",
            DismissedRecord.source_record_id == "permit-dismiss-1",
        )
    ).scalar_one()

    raw_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="permit-dismiss-1",
        raw_payload={"pcis_permit": "permit-dismiss-1"},
        canonical_address="800 DISMISS WAY LOS ANGELES CA 90012",
        identifiers={"permit_number": ["permit-dismiss-1"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-04-01",
            "total_units": 15,
        },
        source_row_hash="dismiss-raw-hash",
    )
    persist_result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[raw_record],
        create_new_candidates=True,
    )
    postgres_session.flush()

    follow_up_review_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == persist_result.source_run_id,
        )
    ).scalars().all()
    orphan_evidence = postgres_session.execute(
        select(Evidence).where(
            Evidence.project_id.is_(None),
            Evidence.source_type == "ladbs_permit",
            Evidence.source_record_id == "permit-dismiss-1",
        )
    ).scalars().all()

    assert result.action == ReviewDecisionAction.REJECT
    assert review_item.status == ReviewItemStatus.REJECTED
    assert dismissed_record.dismissed_by == "nate"
    assert persist_result.new_candidate_review_items == 0
    assert persist_result.possible_match_review_items == 0
    assert persist_result.suppressed_new_candidate_records == 1
    assert persist_result.dismissed_discovery_records_skipped == 1
    assert follow_up_review_items == []
    assert len(orphan_evidence) == 0


def test_defer_review_item_marks_item_deferred(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-defer-1",
    )

    result = defer_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        notes="Need more context.",
    )
    postgres_session.flush()
    postgres_session.refresh(review_item)

    review_decision = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.review_item_id == review_item.id)
    ).scalar_one()

    assert result.action == ReviewDecisionAction.DEFER
    assert review_item.status == ReviewItemStatus.DEFERRED
    assert review_decision.action == ReviewDecisionAction.DEFER
