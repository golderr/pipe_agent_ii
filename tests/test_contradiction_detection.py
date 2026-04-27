from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
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
from tcg_pipeline.review.contradictions import detect_project_contradictions


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

    assert second_result.created_count == 0
    assert second_result.updated_count == 1
    assert [item.id for item in active_items] == [review_item.id]
    assert active_items[0].payload["proposed_value"] == 280


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
    assert review_item.payload["field_name"] == "developer"
    assert review_item.payload["proposed_value"] == "New Dev"


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
            "candidate_evidence_ids": [str(uuid.uuid4())],
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
