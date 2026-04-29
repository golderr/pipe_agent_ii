from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import tcg_pipeline.db.review_workflow as review_workflow
from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    DismissedRecord,
    DismissReason,
    Evidence,
    IdentifierType,
    Jurisdiction,
    Market,
    PipelineStatus,
    Priority,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
    ResearcherOverride,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.db.researcher_overrides import upsert_researcher_overrides
from tcg_pipeline.db.review_workflow import (
    REVIEW_DECISION_STATE_COMMITTED,
    REVIEW_DECISION_STATE_STAGED,
    REVIEW_ITEM_STATE_COMMITTED,
    REVIEW_ITEM_STATE_OPEN,
    REVIEW_ITEM_STATE_STAGED,
    ReviewItemAlreadyStagedError,
    accept_review_item,
    commit_staged_decisions,
    defer_review_item,
    reject_review_item,
    revise_review_decision,
    stage_review_decision,
    unstage_review_decision,
)
from tcg_pipeline.resolution import resolve_project


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
    if not inspector.has_table("researcher_overrides"):
        ResearcherOverride.__table__.create(bind=postgres_session.connection())
    review_item_columns = {
        column["name"] for column in inspector.get_columns("review_items")
    }
    review_decision_columns = {
        column["name"] for column in inspector.get_columns("review_decisions")
    }
    change_log_columns = {
        column["name"] for column in inspector.get_columns("change_log")
    }
    required_columns = {
        "review_items": {"state"},
        "review_decisions": {
            "state",
            "decision_type",
            "staged_at",
            "staged_by",
            "staged_by_email",
            "committed_at",
            "committed_by",
            "committed_by_email",
            "decision_value",
            "decision_notes",
            "source_url",
        },
        "change_log": {"reviewed_by_user_id", "reviewed_by_email"},
    }
    missing_columns = {
        "review_items": required_columns["review_items"] - review_item_columns,
        "review_decisions": required_columns["review_decisions"] - review_decision_columns,
        "change_log": required_columns["change_log"] - change_log_columns,
    }
    missing_columns = {
        table: columns for table, columns in missing_columns.items() if columns
    }
    if missing_columns:
        pytest.skip(
            "Apply the latest migrations before running review workflow tests: "
            f"{missing_columns}"
        )


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


def _add_status_review_item(
    postgres_session: Session,
    *,
    project: Project,
    field_name: str = "total_units",
    old_value: object = 10,
    new_value: object = 20,
) -> ReviewItem:
    review_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        status=ReviewItemStatus.OPEN,
        priority=Priority.HIGH,
        payload={
            "changes": [
                {
                    "field": field_name,
                    "old_value": old_value,
                    "new_value": new_value,
                }
            ],
        },
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    return review_item


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
    table_override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
            ResearcherOverride.cleared_at.is_(None),
        )
    ).scalar_one()
    assert table_override.value == 300
    assert table_override.set_by_label == "nate"
    assert table_override.note == "Researcher confirmed 300 units."
    assert table_override.mode == "until_newer_evidence"
    assert table_override.baseline["evidence_date"] == "2026-04-01"


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


def test_stage_revise_and_unstage_review_decision(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    other_reviewer_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    project = _build_project(
        "712 STAGED DECISION WAY LOS ANGELES CA 90012",
        total_units=10,
    )
    postgres_session.add(project)
    postgres_session.flush()
    review_item = _add_status_review_item(postgres_session, project=project)

    staged = stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="custom",
        decision_value={"value": 30},
        notes="Set 30 units.",
    )
    postgres_session.flush()
    postgres_session.refresh(review_item)

    decision = postgres_session.get(ReviewDecision, staged.decision_id)
    assert decision is not None
    assert staged.revised is False
    assert review_item.state == REVIEW_ITEM_STATE_STAGED
    assert review_item.status == ReviewItemStatus.OPEN
    assert decision.state == REVIEW_DECISION_STATE_STAGED
    assert decision.decision_type == "custom"
    assert decision.decision_value == {"value": 30}
    assert decision.staged_by == reviewer_id
    assert decision.staged_by_email == "reviewer@example.com"

    revised = revise_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="keep_old",
        notes="Keep current value.",
    )
    postgres_session.flush()
    postgres_session.refresh(decision)

    assert revised.decision_id == staged.decision_id
    assert revised.revised is True
    assert decision.decision_type == "keep_old"
    assert decision.decision_notes == "Keep current value."

    with pytest.raises(ReviewItemAlreadyStagedError):
        stage_review_decision(
            postgres_session,
            review_item_id=review_item.id,
            staged_by=other_reviewer_id,
            staged_by_email="other@example.com",
            decision_type="custom",
            decision_value={"value": 40},
        )

    unstaged = unstage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
    )
    postgres_session.flush()
    postgres_session.refresh(review_item)
    remaining_decisions = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.review_item_id == review_item.id)
    ).scalars().all()

    assert unstaged.item_state == REVIEW_ITEM_STATE_OPEN
    assert review_item.state == REVIEW_ITEM_STATE_OPEN
    assert review_item.status == ReviewItemStatus.OPEN
    assert remaining_decisions == []


def test_staged_defer_is_not_committed_and_can_be_unstaged(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    project = _build_project("713 STAGED DEFER WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    review_item = _add_status_review_item(postgres_session, project=project)

    result = stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="defer",
        notes="Needs source review.",
    )
    dry_run = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
        dry_run=True,
    )
    postgres_session.refresh(review_item)

    assert result.decision_type == "defer"
    assert review_item.state == REVIEW_ITEM_STATE_STAGED
    assert review_item.status == ReviewItemStatus.DEFERRED
    assert dry_run.committed_decisions == 0
    assert dry_run.deferred_items == 1

    unstage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
    )
    postgres_session.flush()
    postgres_session.refresh(review_item)

    assert review_item.state == REVIEW_ITEM_STATE_OPEN
    assert review_item.status == ReviewItemStatus.OPEN


def test_commit_staged_decisions_can_scope_to_jurisdiction(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    market = Market(slug="review-scope-market", name="Review Scope", state="CA")
    postgres_session.add(market)
    postgres_session.flush()
    first_jurisdiction = Jurisdiction(
        slug="review-scope-one",
        name="Review Scope One",
        state="CA",
        market_id=market.id,
    )
    second_jurisdiction = Jurisdiction(
        slug="review-scope-two",
        name="Review Scope Two",
        state="CA",
        market_id=market.id,
    )
    postgres_session.add_all([first_jurisdiction, second_jurisdiction])
    postgres_session.flush()
    first_project = _build_project(
        "713 JURISDICTION ONE WAY LOS ANGELES CA 90012",
        total_units=10,
        jurisdiction_id=first_jurisdiction.id,
    )
    second_project = _build_project(
        "713 JURISDICTION TWO WAY LOS ANGELES CA 90012",
        total_units=20,
        jurisdiction_id=second_jurisdiction.id,
    )
    postgres_session.add_all([first_project, second_project])
    postgres_session.flush()
    first_item = _add_status_review_item(
        postgres_session,
        project=first_project,
        old_value=10,
        new_value=30,
    )
    second_item = _add_status_review_item(
        postgres_session,
        project=second_project,
        old_value=20,
        new_value=40,
    )
    stage_review_decision(
        postgres_session,
        review_item_id=first_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="custom",
        decision_value={"value": 30},
    )
    stage_review_decision(
        postgres_session,
        review_item_id=second_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="custom",
        decision_value={"value": 40},
    )

    result = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
        jurisdiction_id=first_jurisdiction.id,
    )
    postgres_session.refresh(first_item)
    postgres_session.refresh(second_item)
    postgres_session.refresh(first_project)
    postgres_session.refresh(second_project)

    assert result.committed_decisions == 1
    assert result.review_items_committed == 1
    assert result.jurisdictions_touched == [first_jurisdiction.id]
    assert first_item.state == REVIEW_ITEM_STATE_COMMITTED
    assert second_item.state == REVIEW_ITEM_STATE_STAGED
    assert first_project.total_units == 30
    assert second_project.total_units == 20


def test_stage_review_decision_translates_unique_race_to_staged_conflict(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    other_reviewer_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    project = _build_project("713 RACE CONFLICT WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    review_item = _add_status_review_item(postgres_session, project=project)
    conflict = ReviewDecision(
        review_item_id=review_item.id,
        action=ReviewDecisionAction.REJECT,
        actor="other@example.com",
        state=REVIEW_DECISION_STATE_STAGED,
        decision_type="keep_old",
        staged_by=other_reviewer_id,
        staged_by_email="other@example.com",
        staged_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
    )
    calls = 0

    def fake_active_staged_decision(
        _session: Session,
        _review_item_id: uuid.UUID,
    ) -> ReviewDecision | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else conflict

    def fake_flush(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("unique staged review decision"))

    monkeypatch.setattr(review_workflow, "_active_staged_decision", fake_active_staged_decision)
    monkeypatch.setattr(postgres_session, "flush", fake_flush)

    with pytest.raises(ReviewItemAlreadyStagedError) as exc_info:
        stage_review_decision(
            postgres_session,
            review_item_id=review_item.id,
            staged_by=reviewer_id,
            staged_by_email="reviewer@example.com",
            decision_type="custom",
            decision_value={"value": 30},
        )

    assert exc_info.value.staged_by == other_reviewer_id
    assert exc_info.value.staged_by_email == "other@example.com"
    assert exc_info.value.decision_type == "keep_old"


def test_stage_review_decision_rejects_candidate_decisions_for_discovery_items(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    candidate = _build_project("713 DISCOVERY CANDIDATE WAY LOS ANGELES CA 90012")
    postgres_session.add(candidate)
    postgres_session.flush()
    _, review_item = _add_discovery_review_item(
        postgres_session,
        item_type=ReviewItemType.POSSIBLE_MATCH,
        candidate_project_ids=[candidate.id],
    )

    with pytest.raises(ValueError, match="not supported for discovery review items"):
        stage_review_decision(
            postgres_session,
            review_item_id=review_item.id,
            staged_by=reviewer_id,
            staged_by_email="reviewer@example.com",
            decision_type="candidate_1",
        )


def test_commit_staged_custom_decision_applies_override_and_audit_identity(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    project = _build_project(
        "714 COMMIT STAGED WAY LOS ANGELES CA 90012",
        total_units=10,
    )
    postgres_session.add(project)
    postgres_session.flush()
    review_item = _add_status_review_item(
        postgres_session,
        project=project,
        old_value=10,
        new_value=20,
    )

    staged = stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="custom",
        decision_value={"value": 25},
        notes="Confirmed 25 units.",
        source_url="https://example.com/source",
    )
    dry_run = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
        dry_run=True,
    )
    postgres_session.refresh(project)
    assert dry_run.committed_decisions == 1
    assert project.total_units == 10

    commit_result = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
    )
    postgres_session.flush()
    postgres_session.refresh(project)
    postgres_session.refresh(review_item)
    decision = postgres_session.get(ReviewDecision, staged.decision_id)
    override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
            ResearcherOverride.cleared_at.is_(None),
        )
    ).scalar_one()
    change_logs = postgres_session.execute(
        select(ChangeLog).where(ChangeLog.review_item_id == review_item.id)
    ).scalars().all()
    total_units_change = next(
        change_log for change_log in change_logs if change_log.field == "total_units"
    )

    assert commit_result.committed_decisions == 1
    assert commit_result.field_changes_applied >= 1
    assert review_item.state == REVIEW_ITEM_STATE_COMMITTED
    assert review_item.status == ReviewItemStatus.ACCEPTED
    assert project.total_units == 25
    assert override.value == 25
    assert override.mode == "review_protected"
    assert override.set_by_user_id == reviewer_id
    assert override.source_url == "https://example.com/source"
    assert decision is not None
    assert decision.state == REVIEW_DECISION_STATE_COMMITTED
    assert decision.committed_by == reviewer_id
    assert decision.committed_by_email == "reviewer@example.com"
    assert total_units_change.reviewed_by == "reviewer@example.com"
    assert total_units_change.reviewed_by_user_id == reviewer_id
    assert total_units_change.reviewed_by_email == "reviewer@example.com"


def test_commit_staged_new_candidate_accept_creates_project(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    mapped_fields = {
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
        "status_evidence_type": "building_permit_issued",
        "status_evidence_date": "2026-04-01",
        "total_units": 120,
    }
    _add_orphan_evidence(
        postgres_session,
        source_record_id="permit-stage-create-1",
        mapped_fields=mapped_fields,
    )
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-stage-create-1",
        item_type=ReviewItemType.NEW_CANDIDATE,
        mapped_fields=mapped_fields,
        canonical_address="715 STAGED CREATE WAY LOS ANGELES CA 90012",
    )

    stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="accept_new",
        decision_value={"create_new": True},
    )
    result = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
    )
    postgres_session.refresh(review_item)

    assert result.committed_decisions == 1
    assert result.affected_projects == 1
    assert review_item.state == REVIEW_ITEM_STATE_COMMITTED
    assert review_item.project_id is not None
    new_project = postgres_session.get(Project, review_item.project_id)
    assert new_project is not None
    assert new_project.canonical_address == "715 STAGED CREATE WAY LOS ANGELES CA 90012"
    assert new_project.pipeline_status == PipelineStatus.APPROVED
    assert new_project.total_units == 120
    linked_evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == "permit-stage-create-1")
    ).scalars().all()
    assert linked_evidence
    assert {row.project_id for row in linked_evidence} == {new_project.id}


def test_commit_staged_possible_match_can_choose_second_candidate(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    first_candidate = _build_project("716 FIRST MATCH WAY LOS ANGELES CA 90012")
    second_candidate = _build_project("717 SECOND MATCH WAY LOS ANGELES CA 90012")
    postgres_session.add_all([first_candidate, second_candidate])
    postgres_session.flush()
    _add_orphan_evidence(postgres_session, source_record_id="permit-second-match-1")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-second-match-1",
        item_type=ReviewItemType.POSSIBLE_MATCH,
        candidate_project_ids=[first_candidate.id, second_candidate.id],
    )

    stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="accept_new",
        decision_value={"project_id": str(second_candidate.id)},
    )
    result = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
    )
    postgres_session.refresh(review_item)

    assert result.committed_decisions == 1
    assert review_item.state == REVIEW_ITEM_STATE_COMMITTED
    assert review_item.project_id == second_candidate.id
    linked_evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == "permit-second-match-1")
    ).scalars().all()
    assert linked_evidence
    assert {row.project_id for row in linked_evidence} == {second_candidate.id}


def test_commit_staged_override_contradiction_accept_new_does_not_invalidate_item(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    project = _build_project(
        "714 CONTRADICTION ACCEPT WAY LOS ANGELES CA 90012",
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="costar",
            source_tier=3,
            ingest_method="manual",
            source_record_id="contradiction-accept-baseline",
            collected_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
            evidence_date=date(2026, 4, 1),
            extracted_fields={"total_units": {"value": 100, "confidence": "medium"}},
        )
    )
    upsert_researcher_overrides(
        postgres_session,
        project,
        {
            "total_units": {
                "value": 212,
                "set_by": "reviewer@example.com",
                "set_at": "2026-04-27T12:00:00+00:00",
                "mode": "review_protected",
                "baseline": {
                    "evidence_date": "2026-04-01",
                    "collected_at": "2026-04-01T12:00:00+00:00",
                    "source_tier": 3,
                    "source_type": "costar",
                },
            }
        },
        set_by_user_id=reviewer_id,
    )
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="costar",
            source_tier=3,
            ingest_method="manual",
            source_record_id="contradiction-accept-newer",
            collected_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            evidence_date=date(2026, 5, 1),
            extracted_fields={"total_units": {"value": 260, "confidence": "medium"}},
        )
    )
    postgres_session.flush()
    resolve_project(project.id, postgres_session, apply=True, write_resolution_log=False)
    postgres_session.flush()
    postgres_session.refresh(project)
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one()

    assert project.total_units == 212
    assert review_item.state == REVIEW_ITEM_STATE_OPEN

    staged = stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="accept_new",
        notes="Accept newer evidence.",
    )
    commit_result = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
    )
    postgres_session.flush()
    postgres_session.refresh(project)
    postgres_session.refresh(review_item)
    decision = postgres_session.get(ReviewDecision, staged.decision_id)
    active_override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
            ResearcherOverride.cleared_at.is_(None),
        )
    ).scalar_one_or_none()
    active_contradiction = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
            ReviewItem.state.in_([REVIEW_ITEM_STATE_OPEN, REVIEW_ITEM_STATE_STAGED]),
        )
    ).scalar_one_or_none()

    assert commit_result.committed_decisions == 1
    assert project.total_units == 260
    assert active_override is None
    assert active_contradiction is None
    assert review_item.state == REVIEW_ITEM_STATE_COMMITTED
    assert review_item.status == ReviewItemStatus.ACCEPTED
    assert review_item.resolved_by == "reviewer@example.com"
    assert decision is not None
    assert decision.state == REVIEW_DECISION_STATE_COMMITTED


def test_commit_staged_decisions_rolls_back_all_decisions_when_one_fails(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    project = _build_project(
        "715 ATOMIC COMMIT WAY LOS ANGELES CA 90012",
        total_units=10,
    )
    postgres_session.add(project)
    postgres_session.flush()
    valid_item = _add_status_review_item(
        postgres_session,
        project=project,
        old_value=10,
        new_value=20,
    )
    invalid_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        status=ReviewItemStatus.OPEN,
        priority=Priority.HIGH,
        payload={},
    )
    postgres_session.add(invalid_item)
    postgres_session.flush()

    stage_review_decision(
        postgres_session,
        review_item_id=valid_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="custom",
        decision_value={"value": 25},
    )
    stage_review_decision(
        postgres_session,
        review_item_id=invalid_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="custom",
        decision_value={"value": 99},
    )
    postgres_session.flush()

    with pytest.raises(ValueError, match="does not identify a field"):
        commit_staged_decisions(
            postgres_session,
            committed_by=reviewer_id,
            committed_by_email="reviewer@example.com",
        )

    postgres_session.refresh(project)
    postgres_session.refresh(valid_item)
    postgres_session.refresh(invalid_item)
    decisions = postgres_session.execute(
        select(ReviewDecision).where(
            ReviewDecision.review_item_id.in_({valid_item.id, invalid_item.id})
        )
    ).scalars().all()
    change_logs = postgres_session.execute(
        select(ChangeLog).where(ChangeLog.review_item_id.in_({valid_item.id, invalid_item.id}))
    ).scalars().all()

    assert project.total_units == 10
    assert valid_item.state == REVIEW_ITEM_STATE_STAGED
    assert invalid_item.state == REVIEW_ITEM_STATE_STAGED
    assert {decision.state for decision in decisions} == {REVIEW_DECISION_STATE_STAGED}
    assert change_logs == []


def test_commit_staged_discovery_reject_preserves_dismiss_reason(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    reviewer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    _, review_item = _add_discovery_review_item(
        postgres_session,
        source_record_id="permit-staged-reject-reason-1",
    )

    stage_review_decision(
        postgres_session,
        review_item_id=review_item.id,
        staged_by=reviewer_id,
        staged_by_email="reviewer@example.com",
        decision_type="keep_old",
        decision_value={"reason": "not_residential"},
        notes="Commercial record.",
    )
    commit_result = commit_staged_decisions(
        postgres_session,
        committed_by=reviewer_id,
        committed_by_email="reviewer@example.com",
    )
    postgres_session.flush()
    dismissed = postgres_session.execute(
        select(DismissedRecord).where(
            DismissedRecord.source == "ladbs_permits",
            DismissedRecord.source_record_id == "permit-staged-reject-reason-1",
        )
    ).scalar_one()

    assert commit_result.committed_decisions == 1
    assert dismissed.reason == DismissReason.NOT_RESIDENTIAL


def test_reject_status_change_creates_review_protected_override_and_newer_evidence_flags_it(
    postgres_session: Session,
) -> None:
    _ensure_review_tables(postgres_session)
    project = _build_project(
        "711 STATUS REJECT WAY LOS ANGELES CA 90012",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add(project)
    postgres_session.flush()

    permit_record = RawRecord(
        source_name="ladbs_permits",
        source_record_id="permit-status-reject-1",
        raw_payload={"pcis_permit": "permit-status-reject-1"},
        canonical_address="711 STATUS REJECT WAY LOS ANGELES CA 90012",
        identifiers={"permit_number": ["permit-status-reject-1"]},
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": "2026-03-15",
        },
        source_row_hash="permit-status-reject-hash",
    )
    collect_result = persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_permits",
        raw_records=[permit_record],
        create_new_candidates=False,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.source_run_id == collect_result.source_run_id,
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
            ReviewItem.field_name == "pipeline_status",
        )
    ).scalar_one()
    assert project.pipeline_status == PipelineStatus.APPROVED

    reject_result = reject_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="nate",
        notes="Permit-only evidence is not enough.",
    )
    postgres_session.flush()
    postgres_session.refresh(project)
    postgres_session.refresh(review_item)

    review_decision = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.review_item_id == review_item.id)
    ).scalar_one()
    rejection_change = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.review_item_id == review_item.id,
            ChangeLog.change_type == ChangeType.RESEARCHER_REJECTED,
        )
    ).scalar_one()

    assert reject_result.action == ReviewDecisionAction.REJECT
    assert review_item.status == ReviewItemStatus.REJECTED
    assert project.pipeline_status == PipelineStatus.PROPOSED
    table_override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "pipeline_status",
        )
    ).scalar_one()
    assert table_override.value == PipelineStatus.PROPOSED.value
    assert table_override.cleared_at is None
    assert table_override.mode == "until_newer_evidence"
    assert table_override.baseline["evidence_date"] == "2026-03-15"
    assert review_decision.field_overrides["pipeline_status"]["mode"] == "until_newer_evidence"
    assert rejection_change.old_value == PipelineStatus.APPROVED.value
    assert rejection_change.new_value == PipelineStatus.PROPOSED.value

    same_evidence_resolution = resolve_project(
        project.id,
        postgres_session,
        apply=True,
        write_resolution_log=False,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    assert project.pipeline_status == PipelineStatus.PROPOSED
    assert (
        same_evidence_resolution.field_resolutions["pipeline_status"].rule_applied
        == "researcher_override_until_newer_evidence"
    )

    inspection_record = RawRecord(
        source_name="ladbs_inspections",
        source_record_id="inspection-status-reject-1",
        raw_payload={"address": "711 STATUS REJECT WAY"},
        canonical_address="711 STATUS REJECT WAY LOS ANGELES CA 90012",
        identifiers={"permit_number": ["permit-status-reject-1"]},
        mapped_fields={
            "status_evidence_type": "building_inspection_recorded",
            "status_evidence_date": "2026-04-10",
        },
        source_row_hash="inspection-status-reject-hash",
    )
    persist_collected_records(
        postgres_session,
        market="los_angeles",
        source_name="ladbs_inspections",
        raw_records=[inspection_record],
        create_new_candidates=False,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    contradiction_review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one()

    assert project.pipeline_status == PipelineStatus.PROPOSED
    postgres_session.refresh(table_override)
    assert table_override.cleared_at is None
    assert contradiction_review_item.state == "open"
    assert contradiction_review_item.status == ReviewItemStatus.OPEN
    assert contradiction_review_item.priority == Priority.HIGH
    assert contradiction_review_item.contradicted_override_id == table_override.id
    assert contradiction_review_item.payload["field_name"] == "pipeline_status"
    assert (
        contradiction_review_item.payload["current_override"]["value"]
        == PipelineStatus.PROPOSED.value
    )
    assert (
        contradiction_review_item.payload["proposed_value"]
        == PipelineStatus.UNDER_CONSTRUCTION.value
    )

    post_contradiction_resolution = resolve_project(
        project.id,
        postgres_session,
        apply=True,
        write_resolution_log=False,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    contradiction_review_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalars().all()

    assert project.pipeline_status == PipelineStatus.PROPOSED
    assert len(contradiction_review_items) == 1
    assert not any(
        review_flag.code == "researcher_override_superseded"
        for review_flag in post_contradiction_resolution.review_flags
    )
