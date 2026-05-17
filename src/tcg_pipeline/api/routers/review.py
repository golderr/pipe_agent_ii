from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
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
    ReviewDedupCreateAndLinkRequest,
    ReviewDedupCreateRequest,
    ReviewDedupMatchRequest,
    ReviewDedupWriteResponse,
    ReviewEvidenceSummary,
    ReviewMatchPreviewResponse,
    ReviewQueueItemResponse,
)
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    AgeRestriction,
    ChangeLog,
    ChangeType,
    Evidence,
    NewsMatchStatus,
    NewsProjectReference,
    PipelineStatus,
    Priority,
    ProductType,
    Project,
    ProjectRelationship,
    RelationshipType,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
)
from tcg_pipeline.db.review_workflow import (
    CHANGELOG_PRIORITY_BY_FIELD,
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
from tcg_pipeline.ingesters._common import build_location
from tcg_pipeline.matching.candidates import (
    DedupSubject,
    FieldDelta,
    compute_subject_candidate_deltas,
    find_dedup_candidates,
    subject_from_news_reference,
)
from tcg_pipeline.review.contradictions import values_contradict
from tcg_pipeline.review.decision_cards import (
    evidence_ids_for_payload,
    field_name_for_payload,
    proposed_value_for_payload,
    upsert_decision_card_review_item,
)
from tcg_pipeline.review.field_metadata import field_metadata_for_review
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
DISCOVERY_RELATIONSHIP_TYPES = {
    RelationshipType.PHASE,
    RelationshipType.MASTER_PLAN,
    RelationshipType.COUNTERPART,
    RelationshipType.SUPERSEDES,
}
REFERENCE_EDIT_FIELD_MAP = {
    "project_name": "candidate_name",
    "canonical_address": "candidate_address",
    "developer": "candidate_developer",
    "total_units": "candidate_unit_total",
    "market_rate_units": "candidate_unit_market_rate",
    "affordable_units": "candidate_unit_affordable",
    "workforce_units": "candidate_unit_workforce",
    "stories": "candidate_stories",
    "product_type": "candidate_product_type",
    "age_restriction": "candidate_age_restriction",
    "pipeline_status": "candidate_status_signal",
    "lat": "candidate_lat",
    "lng": "candidate_lng",
    "city": "candidate_city",
}
INTEGER_PROJECT_FIELDS = {
    "total_units",
    "market_rate_units",
    "affordable_units",
    "workforce_units",
    "stories",
}
FLOAT_PROJECT_FIELDS = {"lat", "lng"}


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


@router.post("/items/{item_id}/match")
def match_review_item_to_project(
    item_id: uuid.UUID,
    payload: ReviewDedupMatchRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewDedupWriteResponse:
    review_item = _load_review_item_for_discovery(session, item_id)
    project = session.get(Project, payload.matched_project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Matched project not found.")

    try:
        with session.begin_nested():
            return _apply_discovery_match(
                session,
                review_item=review_item,
                project=project,
                edits=payload.edits,
                accept_deltas=set(payload.accept_deltas),
                user=user,
            )
    except ValueError as exc:
        _raise_workflow_error(exc)


@router.post("/items/{item_id}/create")
def create_project_from_review_item(
    item_id: uuid.UUID,
    payload: ReviewDedupCreateRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewDedupWriteResponse:
    review_item = _load_review_item_for_discovery(session, item_id)
    try:
        with session.begin_nested():
            project = _create_project_from_discovery_subject(
                session,
                review_item=review_item,
                edits=payload.edits,
                project_fields=payload.project_fields,
                user=user,
            )
            return _apply_discovery_match(
                session,
                review_item=review_item,
                project=project,
                edits={},
                accept_deltas=set(),
                user=user,
                source="discovery_create",
            )
    except ValueError as exc:
        _raise_workflow_error(exc)


@router.post("/items/{item_id}/create-and-link")
def create_project_and_link_from_review_item(
    item_id: uuid.UUID,
    payload: ReviewDedupCreateAndLinkRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ReviewDedupWriteResponse:
    review_item = _load_review_item_for_discovery(session, item_id)
    relationship_type = _coerce_discovery_relationship_type(payload.relationship_type)
    related_project = session.get(Project, payload.related_project_id)
    if related_project is None:
        raise HTTPException(status_code=404, detail="Related project not found.")
    try:
        with session.begin_nested():
            project = _create_project_from_discovery_subject(
                session,
                review_item=review_item,
                edits=payload.edits,
                project_fields=payload.project_fields,
                user=user,
            )
            relationship = ProjectRelationship(
                project_id=project.id,
                related_project_id=related_project.id,
                relationship_type=relationship_type,
            )
            session.add(relationship)
            result = _apply_discovery_match(
                session,
                review_item=review_item,
                project=project,
                edits={},
                accept_deltas=set(),
                user=user,
                source="discovery_create",
                relationship=relationship,
                related_project=related_project,
            )
            result.relationship_id = relationship.id
            return result
    except ValueError as exc:
        _raise_workflow_error(exc)


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


def _apply_discovery_match(
    session: Session,
    *,
    review_item: ReviewItem,
    project: Project,
    edits: dict[str, Any],
    accept_deltas: set[str],
    user: AuthenticatedUser,
    source: str = "discovery_match",
    relationship: ProjectRelationship | None = None,
    related_project: Project | None = None,
) -> ReviewDedupWriteResponse:
    now = datetime.now(UTC)
    actor = _actor_for_audit(user)
    _validate_reference_edit_fields(edits)
    reference = _news_reference_for_review_item(session, review_item)
    if reference is not None:
        _apply_reference_edits(reference, edits, user=user, timestamp=now)

    subject, reference = _dedup_subject_for_review_item(session, review_item)
    deltas = compute_subject_candidate_deltas(subject, project)
    accepted_delta_fields = {delta.field_name for delta in deltas} & accept_deltas
    value_change_items_queued: list[str] = []
    change_log_entries_created = 0

    for delta in deltas:
        if delta.field_name in accepted_delta_fields:
            change_log_entries_created += _apply_delta_to_project(
                session,
                project=project,
                review_item=review_item,
                delta=delta,
                source=source,
                actor=actor,
                user=user,
                timestamp=now,
            )
        elif source != "discovery_create":
            _queue_delta_review_item(
                session,
                project=project,
                review_item=review_item,
                delta=delta,
            )
            value_change_items_queued.append(delta.field_name)

    evidence_rows_reattached = 0
    if reference is not None:
        _mark_reference_matched(
            reference,
            project=project,
            user=user,
            timestamp=now,
            source=source,
        )
        evidence_rows_reattached = _reattach_reference_evidence(
            session,
            reference_id=reference.id,
            project_id=project.id,
        )

    closed_review_items = _close_discovery_review_items(
        session,
        review_item=review_item,
        reference=reference,
        project=project,
        user=user,
        timestamp=now,
        decision_type="create_new" if source == "discovery_create" else "match_to_existing",
    )
    change_log_entries_created += _write_discovery_absorb_change_log(
        session,
        project=project,
        review_item=review_item,
        reference=reference,
        actor=actor,
        user=user,
        timestamp=now,
        source=source,
    )
    if relationship is not None and related_project is not None:
        change_log_entries_created += _write_discovery_relationship_change_log(
            session,
            project=project,
            relationship=relationship,
            related_project=related_project,
            actor=actor,
            user=user,
            timestamp=now,
        )
    project.last_reviewed_by = actor[:50]
    project.last_reviewed_date = now.date()
    project.last_editor = actor[:50]
    project.last_edit_date = now.date()
    session.flush()
    return ReviewDedupWriteResponse(
        review_item_id=review_item.id,
        project_id=project.id,
        reference_id=reference.id if reference is not None else None,
        closed_review_items=closed_review_items,
        evidence_rows_reattached=evidence_rows_reattached,
        value_change_items_queued=value_change_items_queued,
        change_log_entries_created=change_log_entries_created,
        relationship_id=relationship.id if relationship is not None else None,
    )


def _create_project_from_discovery_subject(
    session: Session,
    *,
    review_item: ReviewItem,
    edits: dict[str, Any],
    project_fields: dict[str, Any],
    user: AuthenticatedUser,
) -> Project:
    now = datetime.now(UTC)
    _validate_reference_edit_fields(edits)
    reference = _news_reference_for_review_item(session, review_item)
    if reference is not None:
        _apply_reference_edits(reference, edits, user=user, timestamp=now)
    subject, _reference = _dedup_subject_for_review_item(session, review_item)
    fields = dict(project_fields or {})
    canonical_address = _first_text(fields.get("canonical_address"), subject.canonical_address)
    if canonical_address is None:
        raise ValueError("Cannot create a project without canonical_address.")
    source_obj = getattr(getattr(reference, "article", None), "source", None) if reference else None
    source_run = review_item.source_run
    jurisdiction = getattr(source_obj, "jurisdiction", None) or getattr(
        source_run, "jurisdiction", None
    )
    market = getattr(source_obj, "market", None) or getattr(jurisdiction, "market", None)
    actor = _actor_for_audit(user)
    lat = _coerce_float(fields.get("lat"), subject.lat)
    lng = _coerce_float(fields.get("lng"), subject.lng)
    market_slug = _first_text(
        fields.get("market"),
        getattr(market, "slug", None),
        getattr(source_run, "market", None),
    )
    city = _first_text(
        fields.get("city"),
        getattr(reference, "candidate_city", None),
        getattr(jurisdiction, "display_name", None),
        getattr(jurisdiction, "name", None),
        getattr(market, "display_name", None),
        getattr(market, "name", None),
    )
    state = _first_text(
        fields.get("state"),
        getattr(jurisdiction, "state", None),
        getattr(market, "state", None),
    )
    county = _first_text(
        fields.get("county"),
        getattr(market, "display_name", None),
        getattr(market, "name", None),
        city,
    )
    if market_slug is None or city is None or state is None or county is None:
        raise ValueError(
            "Cannot create a project without market, city, state, and county. "
            "Provide project_fields or attach source_run market metadata."
        )
    project = Project(
        canonical_address=canonical_address,
        raw_addresses=_unique_texts([fields.get("raw_address"), canonical_address]),
        lat=lat,
        lng=lng,
        location=build_location(lat, lng) if lat is not None and lng is not None else None,
        market=market_slug,
        market_id=getattr(market, "id", None) or getattr(jurisdiction, "market_id", None),
        city=city,
        state=state,
        county=county,
        zip=_first_text(fields.get("zip")),
        jurisdiction=getattr(jurisdiction, "name", None),
        jurisdiction_id=getattr(jurisdiction, "id", None),
        project_name=_first_text(fields.get("project_name"), subject.project_name),
        developer=_first_text(fields.get("developer"), subject.developer),
        total_units=_coerce_int(fields.get("total_units"), subject.total_units),
        market_rate_units=_coerce_int(fields.get("market_rate_units"), subject.market_rate_units),
        affordable_units=_coerce_int(fields.get("affordable_units"), subject.affordable_units),
        workforce_units=_coerce_int(fields.get("workforce_units"), subject.workforce_units),
        stories=_coerce_int(fields.get("stories"), subject.building_height_stories),
        product_type=_coerce_product_type(
            _first_text(fields.get("product_type"), subject.product_type)
        ),
        age_restriction=_coerce_age_restriction(
            _first_text(fields.get("age_restriction"), subject.age_restriction)
        ),
        pipeline_status=_coerce_pipeline_status(
            _first_text(fields.get("pipeline_status"), subject.pipeline_status)
        ),
        created_by=actor,
        last_editor=actor[:50],
        last_edit_date=now.date(),
        last_reviewed_by=actor[:50],
        last_reviewed_date=now.date(),
    )
    session.add(project)
    session.flush()
    return project


def _apply_reference_edits(
    reference: NewsProjectReference,
    edits: dict[str, Any],
    *,
    user: AuthenticatedUser,
    timestamp: datetime,
) -> None:
    # Caller must run _validate_reference_edit_fields(edits) first; this trusts the keys.
    for field_name, raw_value in (edits or {}).items():
        target = REFERENCE_EDIT_FIELD_MAP[str(field_name)]
        setattr(reference, target, _coerce_reference_value(target, raw_value))
    reference.manual_relink_by_user_id = user.user_id
    reference.manual_relink_at = timestamp
    reference.manual_relink_note = "Discovery reviewer subject edits applied."


def _validate_reference_edit_fields(edits: dict[str, Any]) -> None:
    unknown_fields = sorted(
        str(field_name)
        for field_name in (edits or {})
        if str(field_name) not in REFERENCE_EDIT_FIELD_MAP
    )
    if unknown_fields:
        allowed = ", ".join(sorted(REFERENCE_EDIT_FIELD_MAP))
        raise HTTPException(
            status_code=422,
            detail=(
                "edits contains unsupported field(s): "
                f"{', '.join(unknown_fields)}. Allowed fields: {allowed}."
            ),
        )


def _coerce_reference_value(target: str, value: Any) -> Any:
    if target in {
        "candidate_unit_total",
        "candidate_unit_market_rate",
        "candidate_unit_affordable",
        "candidate_unit_workforce",
        "candidate_stories",
    }:
        return _coerce_int(value)
    if target in {"candidate_lat", "candidate_lng"}:
        return _coerce_float(value)
    return _first_text(value)


def _mark_reference_matched(
    reference: NewsProjectReference,
    *,
    project: Project,
    user: AuthenticatedUser,
    timestamp: datetime,
    source: str,
) -> None:
    reference.matched_project_id = project.id
    reference.match_status = (
        NewsMatchStatus.CONFIRMED.value
        if source == "discovery_create"
        else NewsMatchStatus.MANUAL_RELINK.value
    )
    reference.match_confidence = 1.0
    reference.match_reason = source
    reference.match_decision_at = timestamp
    reference.manual_relink_by_user_id = user.user_id
    reference.manual_relink_at = timestamp


def _reattach_reference_evidence(
    session: Session,
    *,
    reference_id: uuid.UUID,
    project_id: uuid.UUID,
) -> int:
    rows = (
        session.execute(
            select(Evidence).where(
                Evidence.source_type == "news_article",
                Evidence.source_record_id == str(reference_id),
                Evidence.superseded_at.is_(None),
                or_(Evidence.project_id.is_(None), Evidence.project_id != project_id),
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.project_id = project_id
    return len(rows)


def _apply_delta_to_project(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    delta: FieldDelta,
    source: str,
    actor: str,
    user: AuthenticatedUser,
    timestamp: datetime,
) -> int:
    old_value = _json_safe_project_value(getattr(project, delta.field_name))
    new_value = _coerce_project_field_value(delta.field_name, delta.evidence_value)
    if old_value == _json_safe_project_value(new_value):
        return 0
    setattr(project, delta.field_name, new_value)
    session.add(
        ChangeLog(
            project_id=project.id,
            review_item_id=review_item.id,
            timestamp=timestamp,
            source=source,
            field=delta.field_name,
            old_value=serialize_json(old_value),
            new_value=serialize_json(_json_safe_project_value(new_value)),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=CHANGELOG_PRIORITY_BY_FIELD.get(delta.field_name, Priority.MEDIUM),
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )
    return 1


def _queue_delta_review_item(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    delta: FieldDelta,
) -> None:
    metadata = field_metadata_for_review(delta.field_name)
    payload_mapping = review_item.payload if isinstance(review_item.payload, dict) else {}
    payload = {
        "origin": "discovery_match_delta",
        "source_review_item_id": str(review_item.id),
        "source_record_id": payload_mapping.get("source_record_id"),
        "field_name": delta.field_name,
        "field_label": metadata.label,
        "current_value": delta.current_value,
        "proposed_value": delta.evidence_value,
        "changes": [
            {
                "field": delta.field_name,
                "old_value": delta.current_value,
                "new_value": delta.evidence_value,
            }
        ],
        "match": payload_mapping.get("match"),
        "evidence_ids": [str(review_item.winning_evidence_id)]
        if review_item.winning_evidence_id is not None
        else [],
    }
    upsert_decision_card_review_item(
        session,
        project_id=project.id,
        source_run_id=review_item.source_run_id,
        item_type=ReviewItemType.STATUS_CHANGE,
        field_name=delta.field_name,
        priority=CHANGELOG_PRIORITY_BY_FIELD.get(delta.field_name, Priority.MEDIUM),
        payload=payload,
        proposed_value=delta.evidence_value,
        match_confidence=review_item.match_confidence,
        winning_evidence_id=review_item.winning_evidence_id,
    )


def _close_discovery_review_items(
    session: Session,
    *,
    review_item: ReviewItem,
    reference: NewsProjectReference | None,
    project: Project,
    user: AuthenticatedUser,
    timestamp: datetime,
    decision_type: str,
) -> int:
    items = (
        _same_reference_open_review_items(session, reference.id)
        if reference is not None
        else [review_item]
    )
    actor = _actor_for_audit(user)
    decision_value = serialize_json(
        {
            "project_id": str(project.id),
            "reference_id": str(reference.id) if reference is not None else None,
        }
    )
    for item in items:
        item.project_id = project.id
        item.status = ReviewItemStatus.ACCEPTED
        item.state = REVIEW_ITEM_STATE_COMMITTED
        item.resolved_by = actor[:50]
        item.resolved_at = timestamp
        existing_decision = _active_staged_decision(session, item.id)
        if existing_decision is not None:
            existing_decision.state = REVIEW_DECISION_STATE_COMMITTED
            existing_decision.committed_at = timestamp
            existing_decision.committed_by = user.user_id
            existing_decision.committed_by_email = user.email
            existing_decision.decision_type = decision_type
            existing_decision.decision_value = decision_value
            existing_decision.action = ReviewDecisionAction.ACCEPT
            continue
        session.add(
            ReviewDecision(
                review_item_id=item.id,
                action=ReviewDecisionAction.ACCEPT,
                actor=actor[:50],
                state=REVIEW_DECISION_STATE_COMMITTED,
                decision_type=decision_type,
                staged_at=timestamp,
                staged_by=user.user_id,
                staged_by_email=user.email,
                committed_at=timestamp,
                committed_by=user.user_id,
                committed_by_email=user.email,
                decision_value=decision_value,
            )
        )
    return len(items)


def _same_reference_open_review_items(
    session: Session,
    reference_id: uuid.UUID,
) -> list[ReviewItem]:
    reference_text = str(reference_id)
    return (
        session.execute(
            select(ReviewItem)
            .where(
                ReviewItem.state.in_([REVIEW_ITEM_STATE_OPEN, REVIEW_ITEM_STATE_STAGED]),
                ReviewItem.item_type != ReviewItemType.STATUS_CHANGE,
                or_(
                    ReviewItem.payload["source_record_id"].astext == reference_text,
                    ReviewItem.payload["reference_id"].astext == reference_text,
                    ReviewItem.payload["news_context"]["reference_id"].astext == reference_text,
                ),
            )
            .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
        )
        .scalars()
        .all()
    )


def _active_staged_decision(session: Session, review_item_id: uuid.UUID) -> ReviewDecision | None:
    return session.execute(
        select(ReviewDecision).where(
            ReviewDecision.review_item_id == review_item_id,
            ReviewDecision.state == REVIEW_DECISION_STATE_STAGED,
        )
    ).scalar_one_or_none()


def _write_discovery_absorb_change_log(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    reference: NewsProjectReference | None,
    actor: str,
    user: AuthenticatedUser,
    timestamp: datetime,
    source: str,
) -> int:
    source_label = _source_label_for_review_item(review_item)
    reference_id = str(reference.id) if reference is not None else None
    session.add(
        ChangeLog(
            project_id=project.id,
            review_item_id=review_item.id,
            timestamp=timestamp,
            source=source,
            field="source_reference",
            old_value=None,
            new_value=serialize_json(
                {
                    "summary": (
                        f"Absorbed reference {reference_id or review_item.id} "
                        f"from source {source_label} on {timestamp.date().isoformat()} by {actor}."
                    ),
                    "reference_id": reference_id,
                    "review_item_id": str(review_item.id),
                    "source": source_label,
                }
            ),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.LOW,
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )
    return 1


def _write_discovery_relationship_change_log(
    session: Session,
    *,
    project: Project,
    relationship: ProjectRelationship,
    related_project: Project,
    actor: str,
    user: AuthenticatedUser,
    timestamp: datetime,
) -> int:
    session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=timestamp,
            source="discovery_create",
            field="relationships",
            old_value=None,
            new_value=serialize_json(
                {
                    "relationship_type": relationship.relationship_type.value,
                    "related_project_id": str(related_project.id),
                    "related_project_name": related_project.project_name
                    or related_project.canonical_address,
                    "notes": None,
                }
            ),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.LOW,
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )
    return 1


def _coerce_discovery_relationship_type(value: str) -> RelationshipType:
    try:
        relationship_type = RelationshipType(value.strip())
    except ValueError as exc:
        allowed = ", ".join(sorted(item.value for item in DISCOVERY_RELATIONSHIP_TYPES))
        raise HTTPException(
            status_code=422,
            detail=f"relationship_type must be one of: {allowed}.",
        ) from exc
    if relationship_type not in DISCOVERY_RELATIONSHIP_TYPES:
        allowed = ", ".join(sorted(item.value for item in DISCOVERY_RELATIONSHIP_TYPES))
        raise HTTPException(
            status_code=422,
            detail=f"relationship_type must be one of: {allowed}.",
        )
    return relationship_type


def _coerce_project_field_value(field_name: str, value: Any) -> Any:
    if field_name in INTEGER_PROJECT_FIELDS:
        return _coerce_int(value)
    if field_name in FLOAT_PROJECT_FIELDS:
        return _coerce_float(value)
    if field_name == "pipeline_status":
        return _coerce_pipeline_status(_first_text(value))
    if field_name == "product_type":
        return _coerce_product_type(_first_text(value))
    if field_name == "age_restriction":
        return _coerce_age_restriction(_first_text(value))
    return _first_text(value)


def _coerce_pipeline_status(value: str | None) -> PipelineStatus:
    if value is None:
        return PipelineStatus.PROPOSED
    return PipelineStatus(value)


def _coerce_product_type(value: str | None) -> ProductType:
    if value is None:
        return ProductType.UNKNOWN
    return ProductType(value)


def _coerce_age_restriction(value: str | None) -> AgeRestriction:
    if value is None:
        return AgeRestriction.UNKNOWN
    return AgeRestriction(value)


def _json_safe_project_value(value: Any) -> Any:
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else value


def _actor_for_audit(user: AuthenticatedUser) -> str:
    return user.email or str(user.user_id)


def _source_label_for_review_item(review_item: ReviewItem) -> str:
    if review_item.source_run is not None:
        return review_item.source_run.source_name
    return "review_discovery"


def _unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _first_text(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


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


def _coerce_int(*values: Any) -> int | None:
    return _first_int(*values)


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_float(*values: Any) -> float | None:
    return _first_float(*values)


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
