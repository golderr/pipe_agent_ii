from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    Priority,
    Project,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemType,
)
from tcg_pipeline.review.decision_cards import upsert_decision_card_review_item


def test_upsert_decision_card_merges_same_proposal_evidence(
    postgres_session: Session,
) -> None:
    _ensure_decision_card_columns(postgres_session)
    project = _project("940 DECISION CARD WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()

    first_item, first_created = upsert_decision_card_review_item(
        postgres_session,
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        field_name="pipeline_status",
        priority=Priority.HIGH,
        payload={"proposed_value": "Under Construction", "evidence_ids": ["evidence-1"]},
        proposed_value="Under Construction",
    )
    second_item, second_created = upsert_decision_card_review_item(
        postgres_session,
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        field_name="pipeline_status",
        priority=Priority.HIGH,
        payload={"proposed_value": "Under Construction", "evidence_ids": ["evidence-2"]},
        proposed_value="Under Construction",
    )
    postgres_session.flush()

    active_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.state.in_(["open", "staged"]),
        )
    ).scalars().all()
    assert first_created is True
    assert second_created is False
    assert second_item.id == first_item.id
    assert active_items == [first_item]
    assert first_item.field_name == "pipeline_status"
    assert first_item.payload["evidence_ids"] == ["evidence-1", "evidence-2"]


def test_upsert_decision_card_invalidates_on_proposal_flip_and_drops_stage(
    postgres_session: Session,
) -> None:
    _ensure_decision_card_columns(postgres_session)
    project = _project("941 DECISION CARD FLIP WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()

    first_item, _ = upsert_decision_card_review_item(
        postgres_session,
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        field_name="pipeline_status",
        priority=Priority.HIGH,
        payload={"proposed_value": "Approved", "evidence_ids": ["evidence-1"]},
        proposed_value="Approved",
    )
    postgres_session.flush()
    staged_decision = ReviewDecision(
        review_item_id=first_item.id,
        action=ReviewDecisionAction.ACCEPT,
        actor="reviewer@example.com",
        state="staged",
        decision_type="accept_new",
        staged_by=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        staged_by_email="reviewer@example.com",
    )
    postgres_session.add(staged_decision)
    first_item.state = "staged"
    postgres_session.flush()

    second_item, second_created = upsert_decision_card_review_item(
        postgres_session,
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        field_name="pipeline_status",
        priority=Priority.HIGH,
        payload={"proposed_value": "Under Construction", "evidence_ids": ["evidence-2"]},
        proposed_value="Under Construction",
    )
    postgres_session.flush()

    remaining_decision = postgres_session.execute(
        select(ReviewDecision).where(ReviewDecision.id == staged_decision.id)
    ).scalar_one_or_none()
    assert second_created is True
    assert second_item.id != first_item.id
    assert first_item.state == "invalidated"
    assert first_item.resolved_by == "decision_card_consolidation"
    assert remaining_decision is None


def _ensure_decision_card_columns(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    columns = {column["name"] for column in inspector.get_columns("review_items")}
    missing = {"field_name", "winning_evidence_id"} - columns
    if missing:
        pytest.skip(f"Apply 202604280018 before running decision-card tests: {missing}")


def _project(canonical_address: str) -> Project:
    return Project(
        canonical_address=canonical_address,
        raw_addresses=[canonical_address],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
