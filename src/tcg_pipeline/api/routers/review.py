from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, object_session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.api.schemas import (
    ReviewCommitRequest,
    ReviewCommitResponse,
    ReviewDecisionStageRequest,
    ReviewDecisionStageResponse,
    ReviewDecisionSummary,
    ReviewDedupCandidatesResponse,
    ReviewEvidenceSummary,
    ReviewMatchPreviewResponse,
    ReviewQueueItemResponse,
)
from tcg_pipeline.db.models import (
    Evidence,
    NewsProjectReference,
    Priority,
    Project,
    ReviewDecision,
    ReviewItem,
    ReviewItemType,
)
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
from tcg_pipeline.matching.candidates import (
    DedupSubject,
    compute_subject_candidate_deltas,
    find_dedup_candidates,
    subject_from_news_reference,
)
from tcg_pipeline.review.contradictions import values_contradict
from tcg_pipeline.review.decision_cards import (
    evidence_ids_for_payload,
    field_name_for_payload,
    proposed_value_for_payload,
)
from tcg_pipeline.review.human_summary import human_summary_for_payload
from tcg_pipeline.review.snippets import render_snippet
from tcg_pipeline.review.value_changes import value_change_payload_for_review_item

router = APIRouter(prefix="/review", tags=["review"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)
DISCOVERY_ITEM_TYPES = {
    ReviewItemType.NEW_CANDIDATE.value,
    ReviewItemType.POSSIBLE_MATCH.value,
}


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
    evidence_by_id = _evidence_by_id_for_items(session, items)
    return [_serialize_review_item(item, evidence_by_id=evidence_by_id) for item in items]


@router.get("/queue/{item_id}")
def get_review_item(
    item_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewQueueItemResponse:
    review_item = session.get(ReviewItem, item_id)
    if review_item is None:
        raise HTTPException(status_code=404, detail="Review item not found.")
    evidence_by_id = _evidence_by_id_for_items(session, [review_item])
    return _serialize_review_item(review_item, evidence_by_id=evidence_by_id)


@router.get("/queue/{item_id}/candidates")
def get_review_item_candidates(
    item_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    layer: int | None = Query(default=None, ge=1, le=3),
    include_layer3: bool = False,
    limit: int = Query(default=25, ge=1, le=100),
) -> ReviewDedupCandidatesResponse:
    review_item = _load_review_item_for_discovery(session, item_id)
    subject, _reference = _dedup_subject_for_review_item(session, review_item)
    result = find_dedup_candidates(
        session,
        subject,
        include_layer3=include_layer3 or layer == 3,
        limit=limit,
    )
    payload = result.as_payload()
    if layer is not None:
        payload["candidates"] = [
            candidate
            for candidate in payload["candidates"]
            if int(candidate.get("match_layer") or 0) <= layer
        ]
        payload["new_candidate_probability"] = _new_candidate_probability_for_payload(
            payload["candidates"]
        )
    return ReviewDedupCandidatesResponse(**payload)


@router.get("/items/{item_id}/match-preview")
def get_review_item_match_preview(
    item_id: uuid.UUID,
    candidate_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewMatchPreviewResponse:
    review_item = _load_review_item_for_discovery(session, item_id)
    candidate = session.get(Project, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate project not found.")

    subject, reference = _dedup_subject_for_review_item(session, review_item)
    deltas = compute_subject_candidate_deltas(subject, candidate)
    review_items_to_close = (
        _same_reference_open_review_item_count(session, reference.id)
        if reference is not None
        else 1
    )
    evidence_rows_to_reattach = (
        _evidence_rows_to_reattach_count(session, reference.id, candidate.id)
        if reference is not None
        else 0
    )
    return ReviewMatchPreviewResponse(
        review_items_to_close=review_items_to_close,
        evidence_rows_to_reattach=evidence_rows_to_reattach,
        value_change_items_that_would_be_queued=[delta.field_name for delta in deltas],
    )


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


def _load_review_item_for_discovery(session: Session, item_id: uuid.UUID) -> ReviewItem:
    review_item = session.get(ReviewItem, item_id)
    if review_item is None:
        raise HTTPException(status_code=404, detail="Review item not found.")
    item_type = getattr(review_item.item_type, "value", review_item.item_type)
    if str(item_type) not in DISCOVERY_ITEM_TYPES:
        raise HTTPException(status_code=400, detail="Review item is not a discovery item.")
    return review_item


def _dedup_subject_for_review_item(
    session: Session,
    review_item: ReviewItem,
) -> tuple[DedupSubject, NewsProjectReference | None]:
    reference = _news_reference_for_review_item(session, review_item)
    if reference is not None:
        return subject_from_news_reference(reference.article, reference), reference
    return _fallback_subject_from_payload(review_item), None


def _news_reference_for_review_item(
    session: Session,
    review_item: ReviewItem,
) -> NewsProjectReference | None:
    payload = review_item.payload if isinstance(review_item.payload, dict) else {}
    for reference_id in _reference_ids_from_payload(payload):
        reference = session.get(NewsProjectReference, reference_id)
        if reference is not None:
            return reference
    return None


def _reference_ids_from_payload(payload: dict[str, Any]) -> list[uuid.UUID]:
    raw_values = [
        payload.get("source_record_id"),
        payload.get("reference_id"),
    ]
    news_context = payload.get("news_context")
    if isinstance(news_context, dict):
        raw_values.append(news_context.get("reference_id"))
    reference_ids: list[uuid.UUID] = []
    for raw_value in raw_values:
        try:
            reference_ids.append(uuid.UUID(str(raw_value)))
        except (TypeError, ValueError):
            continue
    return reference_ids


def _fallback_subject_from_payload(review_item: ReviewItem) -> DedupSubject:
    payload = review_item.payload if isinstance(review_item.payload, dict) else {}
    mapped_fields = payload.get("mapped_fields")
    mapped_fields = mapped_fields if isinstance(mapped_fields, dict) else {}
    identifiers = _identifier_mapping(
        payload.get("identifiers") or mapped_fields.get("identifiers")
    )
    subject = DedupSubject(
        project_name=_first_text(
            mapped_fields.get("project_name"),
            mapped_fields.get("name"),
            payload.get("project_name"),
        ),
        canonical_address=_first_text(
            payload.get("canonical_address"),
            mapped_fields.get("canonical_address"),
            mapped_fields.get("address"),
        ),
        developer=_first_text(mapped_fields.get("developer"), payload.get("developer")),
        total_units=_first_int(mapped_fields.get("total_units"), mapped_fields.get("units_total")),
        market_rate_units=_first_int(mapped_fields.get("market_rate_units")),
        affordable_units=_first_int(mapped_fields.get("affordable_units")),
        workforce_units=_first_int(mapped_fields.get("workforce_units")),
        product_type=_first_text(mapped_fields.get("product_type")),
        age_restriction=_first_text(mapped_fields.get("age_restriction")),
        pipeline_status=_first_text(
            mapped_fields.get("pipeline_status"),
            mapped_fields.get("status"),
        ),
        building_height_stories=_first_int(
            mapped_fields.get("stories"),
            mapped_fields.get("building_height_stories"),
        ),
        lat=_first_float(mapped_fields.get("lat"), payload.get("lat")),
        lng=_first_float(mapped_fields.get("lng"), payload.get("lng")),
        market=_first_text(
            getattr(review_item.source_run, "market", None),
            payload.get("market"),
        ),
        jurisdiction_id=getattr(review_item.source_run, "jurisdiction_id", None),
        identifiers=identifiers,
    )
    if not _subject_has_search_signal(subject):
        raise HTTPException(status_code=400, detail="Review item has no dedup subject fields.")
    return subject


def _subject_has_search_signal(subject: DedupSubject) -> bool:
    return any(
        (
            subject.project_name,
            subject.canonical_address,
            subject.developer,
            subject.total_units is not None,
            subject.lat is not None and subject.lng is not None,
            any(subject.identifiers.values()),
        )
    )


def _same_reference_open_review_item_count(
    session: Session,
    reference_id: uuid.UUID,
) -> int:
    reference_text = str(reference_id)
    return int(
        session.execute(
            select(func.count(ReviewItem.id)).where(
                ReviewItem.state.in_([REVIEW_ITEM_STATE_OPEN, REVIEW_ITEM_STATE_STAGED]),
                or_(
                    ReviewItem.payload["source_record_id"].astext == reference_text,
                    ReviewItem.payload["reference_id"].astext == reference_text,
                    ReviewItem.payload["news_context"]["reference_id"].astext == reference_text,
                ),
            )
        ).scalar_one()
    )


def _evidence_rows_to_reattach_count(
    session: Session,
    reference_id: uuid.UUID,
    candidate_project_id: uuid.UUID,
) -> int:
    return int(
        session.execute(
            select(func.count(Evidence.id)).where(
                Evidence.source_type == "news_article",
                Evidence.source_record_id == str(reference_id),
                Evidence.superseded_at.is_(None),
                or_(Evidence.project_id.is_(None), Evidence.project_id != candidate_project_id),
            )
        ).scalar_one()
    )


def _new_candidate_probability_for_payload(candidates: list[dict[str, Any]]) -> float:
    if not candidates:
        return 1.0
    return round(
        max(
            0.0,
            min(
                1.0,
                1.0
                - max(float(candidate.get("match_likelihood") or 0.0) for candidate in candidates),
            ),
        ),
        4,
    )


def _identifier_mapping(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for identifier_type, raw_values in value.items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if cleaned:
            result[str(identifier_type)] = sorted(set(cleaned))
    return result


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _serialize_review_item(
    review_item: ReviewItem,
    *,
    evidence_by_id: dict[uuid.UUID, Evidence] | None = None,
) -> ReviewQueueItemResponse:
    active_decision = _active_decision_for_item(review_item)
    response_payload = _response_payload_for_review_item(review_item)
    evidence_summaries = _serialize_evidence_summaries(
        review_item,
        evidence_by_id=evidence_by_id or {},
    )
    supporting_evidence_ids = [
        str(summary.evidence_id)
        for summary in evidence_summaries
        if summary.stance == "supporting"
    ]
    dissenting_evidence_ids = [
        str(summary.evidence_id)
        for summary in evidence_summaries
        if summary.stance == "against"
    ]
    return ReviewQueueItemResponse(
        id=review_item.id,
        project_id=review_item.project_id,
        source_run_id=review_item.source_run_id,
        item_type=review_item.item_type.value,
        status=review_item.status.value,
        state=review_item.state,
        priority=review_item.priority.value,
        match_confidence=review_item.match_confidence,
        field_name=review_item.field_name,
        winning_evidence_id=review_item.winning_evidence_id,
        payload=response_payload,
        assigned_to=review_item.assigned_to,
        created_at=review_item.created_at.isoformat(),
        resolved_at=review_item.resolved_at.isoformat() if review_item.resolved_at else None,
        resolved_by=review_item.resolved_by,
        active_decision=_serialize_decision(active_decision),
        value_change=value_change_payload_for_review_item(
            review_item,
            payload=response_payload,
            supporting_evidence_ids=supporting_evidence_ids if evidence_summaries else None,
            dissenting_evidence_ids=dissenting_evidence_ids,
        ),
        evidence_summaries=evidence_summaries,
    )


def _response_payload_for_review_item(review_item: ReviewItem) -> dict[str, Any]:
    payload = dict(review_item.payload) if isinstance(review_item.payload, dict) else {}
    payload["human_summary"] = human_summary_for_payload(
        item_type=review_item.item_type,
        payload=payload,
        field_name=review_item.field_name,
    )
    return payload


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


def _evidence_by_id_for_items(
    session: Session,
    items: list[ReviewItem],
) -> dict[uuid.UUID, Evidence]:
    evidence_ids: set[uuid.UUID] = set()
    for item in items:
        payload = item.payload if isinstance(item.payload, dict) else {}
        for evidence_id in evidence_ids_for_payload(payload):
            try:
                evidence_ids.add(uuid.UUID(str(evidence_id)))
            except ValueError:
                continue
    if not evidence_ids:
        return {}
    rows = session.execute(
        select(Evidence).where(Evidence.id.in_(sorted(evidence_ids, key=str)))
    ).scalars().all()
    return {row.id: row for row in rows}


def _serialize_evidence_summaries(
    review_item: ReviewItem,
    *,
    evidence_by_id: dict[uuid.UUID, Evidence],
) -> list[ReviewEvidenceSummary]:
    payload = review_item.payload if isinstance(review_item.payload, dict) else {}
    field_name = review_item.field_name or field_name_for_payload(review_item.item_type, payload)
    proposed_value = proposed_value_for_payload(payload, field_name)
    summaries: list[ReviewEvidenceSummary] = []
    for raw_evidence_id in evidence_ids_for_payload(payload):
        try:
            evidence_id = uuid.UUID(str(raw_evidence_id))
        except ValueError:
            continue
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        snippet = render_snippet(evidence, field_name=field_name)
        extracted_value = _extracted_value(evidence, field_name)
        summaries.append(
            ReviewEvidenceSummary(
                evidence_id=evidence.id,
                stance=_evidence_stance(
                    field_name=field_name,
                    proposed_value=proposed_value,
                    extracted_value=extracted_value,
                    session=object_session(evidence) or None,
                ),
                is_winning=evidence.id == review_item.winning_evidence_id,
                source_type=evidence.source_type,
                source_tier=evidence.source_tier,
                source_record_id=evidence.source_record_id,
                evidence_date=(
                    evidence.evidence_date.isoformat() if evidence.evidence_date else None
                ),
                collected_at=evidence.collected_at.isoformat(),
                summary=snippet.summary,
                detail=snippet.detail,
                source_fields=snippet.source_fields,
                external_link=snippet.external_link,
                highlights=snippet.highlights,
                extracted_value=snippet.fields.extracted_value,
            )
        )
    return summaries


def _evidence_stance(
    *,
    field_name: str | None,
    proposed_value: Any,
    extracted_value: Any,
    session: Session | None,
) -> str:
    if field_name is None or extracted_value is None:
        return "silent"
    return (
        "against"
        if values_contradict(field_name, proposed_value, extracted_value, session=session)
        else "supporting"
    )


def _extracted_value(evidence: Evidence, field_name: str | None) -> Any:
    if field_name is None:
        return None
    extracted_fields = (
        evidence.extracted_fields if isinstance(evidence.extracted_fields, dict) else {}
    )
    payload = extracted_fields.get(field_name)
    if isinstance(payload, dict):
        return payload.get("value")
    return payload


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
