from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.api.schemas import (
    ReviewCommitRequest,
    ReviewCommitResponse,
    ReviewDecisionStageRequest,
    ReviewDecisionStageResponse,
    ReviewDecisionSummary,
    ReviewQueueItemResponse,
)
from tcg_pipeline.db.models import Priority, Project, ReviewDecision, ReviewItem
from tcg_pipeline.db.review_workflow import (
    REVIEW_DECISION_STATE_COMMITTED,
    REVIEW_DECISION_STATE_STAGED,
    REVIEW_ITEM_STATE_COMMITTED,
    REVIEW_ITEM_STATE_OPEN,
    REVIEW_ITEM_STATE_STAGED,
    ReviewItemAlreadyStagedError,
    commit_staged_decisions,
    stage_review_decision,
)
from tcg_pipeline.db.review_workflow import (
    revise_review_decision as revise_review_decision_value,
)
from tcg_pipeline.db.review_workflow import (
    unstage_review_decision as unstage_review_decision_value,
)

router = APIRouter(prefix="/review", tags=["review"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)


@router.get("/queue")
def list_review_queue(
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    state: str | None = Query(default=None, max_length=20),
    jurisdiction_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ReviewQueueItemResponse]:
    states = [_clean_state(state)] if state else [REVIEW_ITEM_STATE_OPEN, REVIEW_ITEM_STATE_STAGED]
    statement = select(ReviewItem).where(ReviewItem.state.in_(states))
    if states == [REVIEW_ITEM_STATE_COMMITTED]:
        statement = statement.order_by(
            ReviewItem.resolved_at.desc().nullslast(),
            ReviewItem.created_at.desc(),
            ReviewItem.id.asc(),
        )
    else:
        statement = statement.order_by(
            case(
                (ReviewItem.priority == Priority.HIGH, 0),
                (ReviewItem.priority == Priority.MEDIUM, 1),
                else_=2,
            ),
            ReviewItem.created_at.asc(),
        )
    if jurisdiction_id is not None:
        statement = statement.join(Project, ReviewItem.project_id == Project.id).where(
            Project.jurisdiction_id == jurisdiction_id
        )
    statement = statement.limit(limit)
    items = session.execute(statement).scalars().all()
    return [_serialize_review_item(item) for item in items]


@router.get("/queue/{item_id}")
def get_review_item(
    item_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewQueueItemResponse:
    review_item = session.get(ReviewItem, item_id)
    if review_item is None:
        raise HTTPException(status_code=404, detail="Review item not found.")
    return _serialize_review_item(review_item)


@router.post("/{item_id}/decide")
def decide_review_item(
    item_id: uuid.UUID,
    payload: ReviewDecisionStageRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewDecisionStageResponse:
    try:
        result = stage_review_decision(
            session,
            review_item_id=item_id,
            staged_by=user.user_id,
            staged_by_email=user.email,
            decision_type=payload.decision_type,
            decision_value=payload.decision_value,
            notes=payload.notes,
            source_url=payload.source_url,
        )
    except ReviewItemAlreadyStagedError as exc:
        _raise_staged_conflict(exc)
    except ValueError as exc:
        _raise_workflow_error(exc)
    return _serialize_stage_result(result)


@router.post("/{item_id}/revise")
def revise_review_item(
    item_id: uuid.UUID,
    payload: ReviewDecisionStageRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewDecisionStageResponse:
    try:
        result = revise_review_decision_value(
            session,
            review_item_id=item_id,
            staged_by=user.user_id,
            staged_by_email=user.email,
            decision_type=payload.decision_type,
            decision_value=payload.decision_value,
            notes=payload.notes,
            source_url=payload.source_url,
        )
    except ReviewItemAlreadyStagedError as exc:
        _raise_staged_conflict(exc)
    except ValueError as exc:
        _raise_workflow_error(exc)
    return _serialize_stage_result(result)


@router.post("/{item_id}/unstage")
def unstage_review_item(
    item_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewDecisionStageResponse:
    try:
        result = unstage_review_decision_value(
            session,
            review_item_id=item_id,
            staged_by=user.user_id,
        )
    except ReviewItemAlreadyStagedError as exc:
        _raise_staged_conflict(exc)
    except ValueError as exc:
        _raise_workflow_error(exc)
    return _serialize_stage_result(result)


@router.post("/commit")
def commit_review_decisions(
    payload: ReviewCommitRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewCommitResponse:
    try:
        result = commit_staged_decisions(
            session,
            committed_by=user.user_id,
            committed_by_email=user.email,
            jurisdiction_id=payload.jurisdiction_id,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        _raise_workflow_error(exc)
    return ReviewCommitResponse(**asdict(result))


def _serialize_stage_result(result) -> ReviewDecisionStageResponse:
    return ReviewDecisionStageResponse(
        review_item_id=result.review_item_id,
        decision_id=result.decision_id,
        decision_type=result.decision_type,
        item_state=result.item_state,
        staged_by=result.staged_by,
        staged_by_email=result.staged_by_email,
        revised=result.revised,
    )


def _serialize_review_item(review_item: ReviewItem) -> ReviewQueueItemResponse:
    active_decision = _active_decision_for_item(review_item)
    return ReviewQueueItemResponse(
        id=review_item.id,
        project_id=review_item.project_id,
        source_run_id=review_item.source_run_id,
        item_type=review_item.item_type.value,
        status=review_item.status.value,
        state=review_item.state,
        priority=review_item.priority.value,
        match_confidence=review_item.match_confidence,
        payload=review_item.payload,
        assigned_to=review_item.assigned_to,
        created_at=review_item.created_at.isoformat(),
        resolved_at=review_item.resolved_at.isoformat() if review_item.resolved_at else None,
        resolved_by=review_item.resolved_by,
        active_decision=_serialize_decision(active_decision),
    )


def _active_decision_for_item(review_item: ReviewItem) -> ReviewDecision | None:
    if review_item.state == REVIEW_ITEM_STATE_COMMITTED:
        committed_decisions = [
            decision
            for decision in review_item.decisions
            if decision.state == REVIEW_DECISION_STATE_COMMITTED
        ]
        if not committed_decisions:
            return None
        # Older/backfilled committed decisions may not have committed_at; created_at
        # keeps the Reviewed tab deterministic without hiding those rows.
        return sorted(
            committed_decisions,
            key=lambda decision: (
                decision.committed_at or decision.created_at,
                decision.created_at,
            ),
        )[-1]

    staged_decisions = [
        decision
        for decision in review_item.decisions
        if decision.state == REVIEW_DECISION_STATE_STAGED
    ]
    if not staged_decisions:
        return None
    return sorted(staged_decisions, key=lambda decision: decision.created_at)[-1]


def _serialize_decision(decision: ReviewDecision | None) -> ReviewDecisionSummary | None:
    if decision is None:
        return None
    return ReviewDecisionSummary(
        decision_id=decision.id,
        state=decision.state,
        decision_type=decision.decision_type,
        staged_at=decision.staged_at.isoformat() if decision.staged_at else None,
        staged_by=decision.staged_by,
        staged_by_email=decision.staged_by_email,
        committed_at=decision.committed_at.isoformat() if decision.committed_at else None,
        committed_by=decision.committed_by,
        committed_by_email=decision.committed_by_email,
        decision_value=decision.decision_value,
        decision_notes=decision.decision_notes,
        source_url=decision.source_url,
    )


def _clean_state(state: str) -> str:
    normalized = state.strip().lower()
    allowed_states = {
        REVIEW_ITEM_STATE_OPEN,
        REVIEW_ITEM_STATE_STAGED,
        "committed",
        "invalidated",
    }
    if normalized not in allowed_states:
        raise HTTPException(status_code=422, detail=f"Unsupported review state: {state}.")
    return normalized


def _raise_staged_conflict(exc: ReviewItemAlreadyStagedError) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "message": str(exc),
            "review_item_id": str(exc.review_item_id),
            "staged_by": str(exc.staged_by) if exc.staged_by is not None else None,
            "staged_by_email": exc.staged_by_email,
            "decision_type": exc.decision_type,
            "staged_at": exc.staged_at.isoformat() if exc.staged_at else None,
        },
    ) from exc


def _raise_workflow_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if "does not exist" in message or "not found" in message.lower() else 400
    raise HTTPException(status_code=status_code, detail=message) from exc
