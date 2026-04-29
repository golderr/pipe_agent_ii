from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    DeveloperAlias,
    DeveloperRegistry,
    Evidence,
    PipelineStatus,
    Priority,
    Project,
    ResearcherOverride,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    StatusConfidence,
)
from tcg_pipeline.resolution.fields import FieldResolution
from tcg_pipeline.review.contradictions import (
    detect_project_contradictions,
    values_contradict,
)


def test_detect_project_contradictions_creates_and_updates_single_active_item(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("920 CONTRADICTION SERVICE WAY LOS ANGELES CA 90012", total_units=212)
    postgres_session.add(project)
    postgres_session.flush()
    override = _override(project, field_name="total_units", value=212)
    postgres_session.add(override)
    postgres_session.flush()

    first_result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={
            "total_units": _resolution(
                field_name="total_units",
                override_value=212,
                candidate_value=260,
            )
        },
    )
    postgres_session.flush()

    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one()

    assert first_result.created_count == 1
    assert first_result.updated_count == 0
    assert review_item.state == "open"
    assert review_item.status == ReviewItemStatus.OPEN
    assert review_item.contradicted_override_id == override.id
    assert review_item.payload["proposed_value"] == 260

    second_result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={
            "total_units": _resolution(
                field_name="total_units",
                override_value=212,
                candidate_value=280,
            )
        },
    )
    postgres_session.flush()
    active_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
            ReviewItem.state.in_(["open", "staged"]),
        )
    ).scalars().all()
    invalidated_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
            ReviewItem.state == "invalidated",
        )
    ).scalars().all()

    assert second_result.created_count == 1
    assert second_result.updated_count == 0
    assert second_result.invalidated_count == 1
    assert [item.id for item in invalidated_items] == [review_item.id]
    assert len(active_items) == 1
    assert active_items[0].id != review_item.id
    assert active_items[0].payload["proposed_value"] == 280
    assert active_items[0].field_name == "total_units"


def test_detect_project_contradictions_flags_baseline_less_legacy_override(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("923 LEGACY CONTRADICTION WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_override(project, field_name="developer", value="Legacy Dev"))
    postgres_session.flush()

    result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={
            "developer": _resolution(
                field_name="developer",
                override_value="Legacy Dev",
                candidate_value="New Dev",
                mode="sticky",
                include_baseline=False,
                candidate_is_newer=False,
            )
        },
    )
    postgres_session.flush()
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one()

    assert result.created_count == 1
    assert review_item.field_name == "developer"
    assert review_item.payload["field_name"] == "developer"
    assert review_item.payload["proposed_value"] == "New Dev"
    assert review_item.payload["evidence_ids"]


def test_detect_project_contradictions_ignores_developer_legal_suffix_noise(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("924 DEVELOPER SUFFIX WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_override(project, field_name="developer", value="Helio Capital LLC"))
    postgres_session.flush()

    result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={
            "developer": _resolution(
                field_name="developer",
                override_value="Helio Capital LLC",
                candidate_value="Helio Capital, LLC",
            )
        },
    )
    postgres_session.flush()
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one_or_none()

    assert result.created_count == 0
    assert review_item is None


def test_detect_project_contradictions_uses_developer_registry_aliases(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("925 DEVELOPER ALIAS WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    developer = DeveloperRegistry(canonical_name="Helio Group")
    postgres_session.add(developer)
    postgres_session.flush()
    postgres_session.add(
        DeveloperAlias(
            developer_id=developer.id,
            alias_name="Helio Capital LLC",
        )
    )
    postgres_session.add(_override(project, field_name="developer", value="Helio Capital LLC"))
    postgres_session.flush()

    result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={
            "developer": _resolution(
                field_name="developer",
                override_value="Helio Capital LLC",
                candidate_value="Helio Group",
            )
        },
    )
    postgres_session.flush()
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one_or_none()

    assert result.created_count == 0
    assert review_item is None


def test_unit_string_and_integer_values_do_not_contradict_when_equal() -> None:
    assert values_contradict("total_units", "120", 120) is False
    assert values_contradict("total_units", "120", "126") is True


def test_pipeline_status_supporting_evidence_uses_resolved_status_values(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("926 STATUS SUPPORT WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_override(project, field_name="pipeline_status", value="Approved"))
    supporting_evidence = Evidence(
        project_id=project.id,
        source_type="ladbs_permit",
        source_tier=1,
        ingest_method="manual",
        source_record_id="status-supporting",
        collected_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        extracted_fields={"pipeline_status": {"value": "Approved", "confidence": "high"}},
    )
    candidate_evidence = Evidence(
        project_id=project.id,
        source_type="ladbs_inspection",
        source_tier=1,
        ingest_method="manual",
        source_record_id="status-candidate",
        collected_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        extracted_fields={
            "pipeline_status": {
                "value": "Under Construction",
                "confidence": "high",
            }
        },
    )
    postgres_session.add_all([supporting_evidence, candidate_evidence])
    postgres_session.flush()

    result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={
            "pipeline_status": _resolution(
                field_name="pipeline_status",
                override_value="Approved",
                candidate_value="Under Construction",
                candidate_evidence_ids=[candidate_evidence.id],
            )
        },
    )
    postgres_session.flush()
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one()

    assert result.created_count == 1
    assert review_item.field_name == "pipeline_status"
    assert review_item.winning_evidence_id == candidate_evidence.id
    assert set(review_item.payload["evidence_ids"]) == {
        str(candidate_evidence.id),
        str(supporting_evidence.id),
    }


def test_detect_project_contradictions_invalidates_stale_item_and_drops_staged_decision(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("921 STALE CONTRADICTION WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    review_item = _review_item(project, state="staged", status=ReviewItemStatus.DEFERRED)
    postgres_session.add(review_item)
    postgres_session.flush()
    decision = _staged_decision(review_item)
    postgres_session.add(decision)
    postgres_session.flush()

    result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={},
    )
    postgres_session.flush()
    remaining_decision = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.id == decision.id)
    ).scalar_one_or_none()

    assert result.invalidated_count == 1
    assert review_item.state == "invalidated"
    assert review_item.status == ReviewItemStatus.OPEN
    assert review_item.resolved_by == "contradiction_detection"
    assert remaining_decision is None


def test_detect_project_contradictions_preserves_skipped_staged_item(
    postgres_session: Session,
) -> None:
    _ensure_contradiction_tables(postgres_session)
    project = _project("922 SKIPPED CONTRADICTION WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    review_item = _review_item(project, state="staged", status=ReviewItemStatus.OPEN)
    postgres_session.add(review_item)
    postgres_session.flush()
    decision = _staged_decision(review_item)
    postgres_session.add(decision)
    postgres_session.flush()

    result = detect_project_contradictions(
        postgres_session,
        project=project,
        field_resolutions={},
        skip_review_item_ids={review_item.id},
    )
    postgres_session.flush()
    remaining_decision = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.id == decision.id)
    ).scalar_one_or_none()

    assert result.invalidated_count == 0
    assert review_item.state == "staged"
    assert remaining_decision is not None


def _ensure_contradiction_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "projects",
        "researcher_overrides",
        "review_items",
        "review_decisions",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply the latest migrations before running contradiction tests: {missing}")
    columns = {column["name"] for column in inspector.get_columns("review_items")}
    missing_columns = {"field_name", "winning_evidence_id"} - columns
    if missing_columns:
        pytest.skip(
            f"Apply the latest migrations before running contradiction tests: {missing_columns}"
        )


def _project(canonical_address: str, **overrides: Any) -> Project:
    defaults: dict[str, Any] = {
        "raw_addresses": [canonical_address],
        "market": "los_angeles",
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
        "pipeline_status": PipelineStatus.PROPOSED,
    }
    defaults.update(overrides)
    return Project(canonical_address=canonical_address, **defaults)


def _override(project: Project, *, field_name: str, value: Any) -> ResearcherOverride:
    return ResearcherOverride(
        project_id=project.id,
        field_name=field_name,
        value=value,
        set_by_user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        set_by_label="reviewer@example.com",
        set_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        mode="review_protected",
        baseline={
            "evidence_date": "2026-04-01",
            "collected_at": "2026-04-01T12:00:00+00:00",
            "source_tier": 3,
            "source_type": "costar",
        },
    )


def _resolution(
    *,
    field_name: str,
    override_value: Any,
    candidate_value: Any,
    mode: str = "review_protected",
    include_baseline: bool = True,
    candidate_is_newer: bool = True,
    candidate_evidence_ids: list[uuid.UUID | str] | None = None,
) -> FieldResolution:
    baseline = (
        {
            "evidence_date": "2026-04-01",
            "collected_at": "2026-04-01T12:00:00+00:00",
            "source_tier": 3,
            "source_type": "costar",
        }
        if include_baseline
        else None
    )
    return FieldResolution(
        field_name=field_name,
        value=override_value,
        confidence=StatusConfidence.HIGH,
        evidence_ids=[],
        rule_applied="researcher_override",
        metadata={
            "mode": mode,
            "set_by": "reviewer@example.com",
            "set_at": "2026-04-27T12:00:00+00:00",
            "baseline": baseline,
            "candidate_value": candidate_value,
            "candidate_rule_applied": "most_recent_wins",
            "candidate_confidence": "medium",
            "candidate_evidence_ids": [
                str(evidence_id)
                for evidence_id in (candidate_evidence_ids or [uuid.uuid4()])
            ],
            "candidate_evidence_date": "2026-05-01",
            "candidate_evidence_frontier": {
                "evidence_date": "2026-05-01",
                "collected_at": "2026-05-01T12:00:00+00:00",
                "source_tier": 3,
                "source_type": "costar",
            },
            "candidate_is_newer_than_baseline": candidate_is_newer,
        },
    )


def _review_item(
    project: Project,
    *,
    state: str,
    status: ReviewItemStatus,
) -> ReviewItem:
    return ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.OVERRIDE_CONTRADICTION,
        status=status,
        state=state,
        priority=Priority.MEDIUM,
        field_name="total_units",
        payload={"field_name": "total_units"},
        contradiction_priority="medium",
    )


def _staged_decision(review_item: ReviewItem) -> ReviewDecision:
    return ReviewDecision(
        review_item_id=review_item.id,
        action=ReviewDecisionAction.ACCEPT,
        actor="reviewer@example.com",
        state="staged",
        decision_type="accept_new",
        staged_by=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        staged_by_email="reviewer@example.com",
        staged_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
    )
