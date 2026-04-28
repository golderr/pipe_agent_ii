from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    DismissedRecord,
    DismissReason,
    Evidence,
    IdentifierType,
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
from tcg_pipeline.db.researcher_overrides import (
    clear_researcher_override_fields,
    upsert_researcher_overrides,
)
from tcg_pipeline.ingesters._common import serialize_json_value
from tcg_pipeline.matching.differ import (
    DiffResult,
    ReviewFlag,
    diff_project_snapshots,
    snapshot_project_for_diff,
)
from tcg_pipeline.resolution import ProjectResolutionResult, resolve_project
from tcg_pipeline.resolution.engine import normalize_value_for_project
from tcg_pipeline.resolution.fields import FieldResolution
from tcg_pipeline.source_tiers import get_logical_source_type

DISCOVERY_REVIEW_ITEM_TYPES = {
    ReviewItemType.NEW_CANDIDATE,
    ReviewItemType.POSSIBLE_MATCH,
}
CHANGELOG_PRIORITY_BY_FIELD = {
    "pipeline_status": Priority.HIGH,
    "total_units": Priority.MEDIUM,
    "affordable_units": Priority.MEDIUM,
    "market_rate_units": Priority.MEDIUM,
    "product_type": Priority.MEDIUM,
    "date_delivery": Priority.MEDIUM,
    "age_restriction": Priority.MEDIUM,
    "developer": Priority.MEDIUM,
    "delivery_year_provenance": Priority.LOW,
    "likelihood": Priority.LOW,
    "likelihood_breakdown": Priority.LOW,
    "confidence": Priority.LOW,
    "confidence_reason": Priority.LOW,
    "status_confidence": Priority.LOW,
    "last_evidence_date": Priority.LOW,
}
CHANGELOG_TRACKED_FIELDS = tuple(CHANGELOG_PRIORITY_BY_FIELD)

REVIEW_ITEM_STATE_OPEN = "open"
REVIEW_ITEM_STATE_STAGED = "staged"
REVIEW_ITEM_STATE_COMMITTED = "committed"
# C.i owns invalidating open/staged items when newer evidence or project state
# makes the original review proposal no longer relevant.
REVIEW_ITEM_STATE_INVALIDATED = "invalidated"

REVIEW_DECISION_STATE_STAGED = "staged"
REVIEW_DECISION_STATE_COMMITTED = "committed"

DECISION_ACCEPT_NEW = "accept_new"
DECISION_KEEP_OLD = "keep_old"
DECISION_CUSTOM = "custom"
DECISION_DEFER = "defer"
DECISION_CANDIDATE_PREFIX = "candidate_"

STAGEABLE_DECISION_TYPES = {
    DECISION_ACCEPT_NEW,
    DECISION_KEEP_OLD,
    DECISION_CUSTOM,
    DECISION_DEFER,
}
DISCOVERY_STAGEABLE_DECISION_TYPES = {
    DECISION_ACCEPT_NEW,
    DECISION_KEEP_OLD,
    DECISION_DEFER,
}


@dataclass(frozen=True, slots=True)
class IdentifierConflict:
    identifier_type: IdentifierType
    value: str
    owner_project_id: uuid.UUID


@dataclass(slots=True)
class EvidenceLinkResult:
    evidence_rows: list[Evidence] = field(default_factory=list)
    linked_count: int = 0


@dataclass(slots=True)
class ReviewWorkflowResult:
    review_item_id: uuid.UUID
    action: ReviewDecisionAction
    project_id: uuid.UUID | None = None
    linked_evidence_count: int = 0
    source_record_created: bool = False
    source_record_updated: bool = False
    identifiers_inserted: int = 0
    change_log_entries_created: int = 0
    follow_up_review_items_created: int = 0
    identifier_conflicts: list[IdentifierConflict] = field(default_factory=list)


@dataclass(slots=True)
class ReviewStageResult:
    review_item_id: uuid.UUID
    decision_id: uuid.UUID
    decision_type: str
    item_state: str
    staged_by: uuid.UUID | None
    staged_by_email: str | None
    revised: bool = False


@dataclass(slots=True)
class ReviewCommitResult:
    committed_decisions: int = 0
    affected_projects: int = 0
    field_changes_applied: int = 0
    review_items_committed: int = 0
    review_items_remaining: int = 0
    deferred_items: int = 0
    jurisdictions_touched: list[uuid.UUID] = field(default_factory=list)
    queue_cleared: bool = False
    dry_run: bool = False


class ReviewItemAlreadyStagedError(ValueError):
    def __init__(
        self,
        *,
        review_item_id: uuid.UUID,
        staged_by: uuid.UUID | None,
        staged_by_email: str | None,
        decision_type: str | None,
        staged_at: datetime | None,
    ) -> None:
        super().__init__(f"Review item {review_item_id} is already staged.")
        self.review_item_id = review_item_id
        self.staged_by = staged_by
        self.staged_by_email = staged_by_email
        self.decision_type = decision_type
        self.staged_at = staged_at


def accept_review_item(
    session: Session,
    *,
    review_item_id: uuid.UUID,
    actor: str,
    project_id: uuid.UUID | None = None,
    create_new: bool = False,
    notes: str | None = None,
    field_overrides: Mapping[str, Any] | None = None,
    new_project_data: Mapping[str, Any] | None = None,
) -> ReviewWorkflowResult:
    review_item = _load_open_review_item(session, review_item_id)
    if review_item.item_type not in DISCOVERY_REVIEW_ITEM_TYPES:
        raise ValueError(
            "Accept is only supported for discovery review items (new_candidate, possible_match)."
        )
    if create_new == (project_id is not None):
        raise ValueError("Provide exactly one of project_id or create_new=True.")

    source_run = _load_source_run(review_item)
    payload = _payload_mapping(review_item.payload)
    source_record_id = _required_payload_text(payload, "source_record_id")
    source_name = source_run.source_name
    source_type = get_logical_source_type(source_name)
    now = datetime.now(UTC)

    if create_new:
        project = _build_project_from_review_item(
            review_item=review_item,
            source_run=source_run,
            actor=actor,
            new_project_data=new_project_data,
        )
        session.add(project)
        session.flush()
    else:
        assert project_id is not None
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} does not exist.")
        _validate_possible_match_choice(review_item, project.id)

    previous_values = _capture_project_values(project)
    previous_snapshot = snapshot_project_for_diff(project)
    evidence_link = _link_orphan_evidence(
        session,
        project_id=project.id,
        source_type=source_type,
        source_record_id=source_record_id,
    )
    source_record_created, source_record_updated = _upsert_project_source_record(
        session,
        project=project,
        review_item=review_item,
        source_name=source_name,
        source_record_id=source_record_id,
        evidence_rows=evidence_link.evidence_rows,
    )
    identifiers_inserted, identifier_conflicts = _persist_review_identifiers(
        session,
        project=project,
        payload=payload,
    )
    session.flush()
    pre_override_resolution = resolve_project(
        project.id,
        session,
        apply=False,
        write_resolution_log=False,
    )

    normalized_overrides = _normalize_field_overrides(
        field_overrides,
        actor=actor,
        note=notes,
        now=now,
        candidate_resolutions=pre_override_resolution.field_resolutions,
    )
    if normalized_overrides:
        upsert_researcher_overrides(session, project, normalized_overrides)

    project.last_reviewed_by = actor
    project.last_reviewed_date = now.date()

    session.flush()
    resolution_result = resolve_project(
        project.id,
        session,
        apply=True,
        write_resolution_log=True,
    )
    change_log_entries_created = _write_accept_change_logs(
        session,
        project=project,
        review_item=review_item,
        source_name=source_name,
        actor=actor,
        previous_values=previous_values,
        resolution_result=resolution_result,
        timestamp=now,
    )
    follow_up_review_items_created = _create_follow_up_review_item(
        session,
        project=project,
        review_item=review_item,
        previous_snapshot=previous_snapshot,
        resolution_result=resolution_result,
    )

    session.add(
        ReviewDecision(
            review_item_id=review_item.id,
            action=ReviewDecisionAction.ACCEPT,
            actor=actor,
            notes=notes,
            field_overrides=normalized_overrides or None,
            state=REVIEW_DECISION_STATE_COMMITTED,
            decision_type=DECISION_ACCEPT_NEW,
            staged_at=now,
            staged_by_email=actor,
            committed_at=now,
            committed_by_email=actor,
            decision_value=normalized_overrides or None,
            decision_notes=notes,
        )
    )
    review_item.status = ReviewItemStatus.ACCEPTED
    review_item.state = REVIEW_ITEM_STATE_COMMITTED
    review_item.resolved_by = actor
    review_item.resolved_at = now
    review_item.project_id = project.id

    return ReviewWorkflowResult(
        review_item_id=review_item.id,
        action=ReviewDecisionAction.ACCEPT,
        project_id=project.id,
        linked_evidence_count=evidence_link.linked_count,
        source_record_created=source_record_created,
        source_record_updated=source_record_updated,
        identifiers_inserted=identifiers_inserted,
        change_log_entries_created=change_log_entries_created,
        follow_up_review_items_created=follow_up_review_items_created,
        identifier_conflicts=identifier_conflicts,
    )


def reject_review_item(
    session: Session,
    *,
    review_item_id: uuid.UUID,
    actor: str,
    notes: str | None = None,
    reason: DismissReason = DismissReason.OTHER,
) -> ReviewWorkflowResult:
    review_item = _load_open_review_item(session, review_item_id)
    now = datetime.now(UTC)
    source_run = review_item.source_run
    payload = _payload_mapping(review_item.payload)
    generated_field_overrides = None

    if review_item.item_type in DISCOVERY_REVIEW_ITEM_TYPES and source_run is not None:
        source_record_id = _required_payload_text(payload, "source_record_id")
        dismissed = _find_dismissed_record(
            session,
            source_name=source_run.source_name,
            source_record_id=source_record_id,
        )
        if dismissed is None:
            session.add(
                DismissedRecord(
                    source=source_run.source_name,
                    source_record_id=source_record_id,
                    canonical_address=_optional_payload_text(payload, "canonical_address"),
                    reason=reason,
                    dismissed_by=actor,
                    notes=notes,
                )
            )
    elif (
        review_item.item_type == ReviewItemType.STATUS_CHANGE
        and review_item.project_id is not None
    ):
        project = session.get(Project, review_item.project_id)
        if project is not None:
            generated_field_overrides = _build_status_rejection_override(
                session,
                project=project,
                review_item=review_item,
                actor=actor,
                note=notes,
                now=now,
            )
            if generated_field_overrides:
                previous_status = normalize_value_for_project(project.pipeline_status)
                upsert_researcher_overrides(session, project, generated_field_overrides)
                project.last_reviewed_by = actor
                project.last_reviewed_date = now.date()
                session.flush()
                resolution_result = resolve_project(
                    project.id,
                    session,
                    apply=True,
                    write_resolution_log=True,
                )
                new_status = normalize_value_for_project(
                    resolution_result.resolved_values.get("pipeline_status")
                )
                if previous_status != new_status:
                    session.add(
                        ChangeLog(
                            project_id=project.id,
                            review_item_id=review_item.id,
                            timestamp=now,
                            source=(
                                source_run.source_name
                                if source_run is not None
                                else "review_workflow"
                            ),
                            field="pipeline_status",
                            old_value=previous_status,
                            new_value=new_status,
                            change_type=ChangeType.RESEARCHER_REJECTED,
                            priority=Priority.HIGH,
                            reviewed_by=actor,
                        )
                    )

    session.add(
        ReviewDecision(
            review_item_id=review_item.id,
            action=ReviewDecisionAction.REJECT,
            actor=actor,
            notes=notes,
            field_overrides=generated_field_overrides,
            state=REVIEW_DECISION_STATE_COMMITTED,
            decision_type=DECISION_KEEP_OLD,
            staged_at=now,
            staged_by_email=actor,
            committed_at=now,
            committed_by_email=actor,
            decision_value=generated_field_overrides,
            decision_notes=notes,
        )
    )
    review_item.status = ReviewItemStatus.REJECTED
    review_item.state = REVIEW_ITEM_STATE_COMMITTED
    review_item.resolved_by = actor
    review_item.resolved_at = now

    return ReviewWorkflowResult(
        review_item_id=review_item.id,
        action=ReviewDecisionAction.REJECT,
        project_id=review_item.project_id,
    )


def defer_review_item(
    session: Session,
    *,
    review_item_id: uuid.UUID,
    actor: str,
    notes: str | None = None,
) -> ReviewWorkflowResult:
    review_item = _load_open_review_item(session, review_item_id)
    now = datetime.now(UTC)
    session.add(
        ReviewDecision(
            review_item_id=review_item.id,
            action=ReviewDecisionAction.DEFER,
            actor=actor,
            notes=notes,
            state=REVIEW_DECISION_STATE_STAGED,
            decision_type=DECISION_DEFER,
            staged_at=now,
            staged_by_email=actor,
            decision_notes=notes,
        )
    )
    review_item.status = ReviewItemStatus.DEFERRED
    review_item.state = REVIEW_ITEM_STATE_STAGED
    review_item.resolved_by = actor
    review_item.resolved_at = now
    return ReviewWorkflowResult(
        review_item_id=review_item.id,
        action=ReviewDecisionAction.DEFER,
        project_id=review_item.project_id,
    )


def stage_review_decision(
    session: Session,
    *,
    review_item_id: uuid.UUID,
    staged_by: uuid.UUID,
    staged_by_email: str | None,
    decision_type: str,
    decision_value: Any | None = None,
    notes: str | None = None,
    source_url: str | None = None,
) -> ReviewStageResult:
    """Stage or revise a user's decision without applying it to project state."""

    normalized_decision_type = _normalize_decision_type(decision_type)
    review_item = _load_stageable_review_item(session, review_item_id)
    if (
        review_item.item_type in DISCOVERY_REVIEW_ITEM_TYPES
        and normalized_decision_type not in DISCOVERY_STAGEABLE_DECISION_TYPES
    ):
        raise ValueError(
            f"{normalized_decision_type} is not supported for discovery review items."
        )
    now = datetime.now(UTC)
    existing = _active_staged_decision(session, review_item.id)
    revised = existing is not None

    try:
        with session.begin_nested():
            if existing is not None:
                if not _can_modify_staged_decision(existing, staged_by):
                    raise ReviewItemAlreadyStagedError(
                        review_item_id=review_item.id,
                        staged_by=existing.staged_by,
                        staged_by_email=existing.staged_by_email,
                        decision_type=existing.decision_type,
                        staged_at=existing.staged_at,
                    )
                decision = existing
                decision.action = _legacy_action_for_decision_type(normalized_decision_type)
                decision.actor = _actor_for_decision(
                    staged_by=staged_by,
                    staged_by_email=staged_by_email,
                )
                decision.notes = notes
                decision.field_overrides = serialize_json(decision_value)
                decision.decision_type = normalized_decision_type
                decision.staged_at = now
                decision.staged_by = staged_by
                decision.staged_by_email = staged_by_email
                decision.decision_value = serialize_json(decision_value)
                decision.decision_notes = notes
                decision.source_url = _coerce_text(source_url)
            else:
                decision = ReviewDecision(
                    review_item_id=review_item.id,
                    action=_legacy_action_for_decision_type(normalized_decision_type),
                    actor=_actor_for_decision(
                        staged_by=staged_by,
                        staged_by_email=staged_by_email,
                    ),
                    notes=notes,
                    field_overrides=serialize_json(decision_value),
                    state=REVIEW_DECISION_STATE_STAGED,
                    decision_type=normalized_decision_type,
                    staged_at=now,
                    staged_by=staged_by,
                    staged_by_email=staged_by_email,
                    decision_value=serialize_json(decision_value),
                    decision_notes=notes,
                    source_url=_coerce_text(source_url),
                )
                session.add(decision)

            review_item.state = REVIEW_ITEM_STATE_STAGED
            if normalized_decision_type == DECISION_DEFER:
                review_item.status = ReviewItemStatus.DEFERRED
                review_item.resolved_by = _actor_for_decision(
                    staged_by=staged_by,
                    staged_by_email=staged_by_email,
                )
                review_item.resolved_at = now
            else:
                review_item.status = ReviewItemStatus.OPEN
                review_item.resolved_by = None
                review_item.resolved_at = None
            session.flush()
    except IntegrityError as exc:
        conflict = _active_staged_decision(session, review_item_id)
        if conflict is None:
            raise
        raise ReviewItemAlreadyStagedError(
            review_item_id=review_item_id,
            staged_by=conflict.staged_by,
            staged_by_email=conflict.staged_by_email,
            decision_type=conflict.decision_type,
            staged_at=conflict.staged_at,
        ) from exc

    return ReviewStageResult(
        review_item_id=review_item.id,
        decision_id=decision.id,
        decision_type=normalized_decision_type,
        item_state=review_item.state,
        staged_by=decision.staged_by,
        staged_by_email=decision.staged_by_email,
        revised=revised,
    )

def revise_review_decision(
    session: Session,
    *,
    review_item_id: uuid.UUID,
    staged_by: uuid.UUID,
    staged_by_email: str | None,
    decision_type: str,
    decision_value: Any | None = None,
    notes: str | None = None,
    source_url: str | None = None,
) -> ReviewStageResult:
    if _active_staged_decision(session, review_item_id) is None:
        raise ValueError(f"Review item {review_item_id} has no staged decision to revise.")
    return stage_review_decision(
        session,
        review_item_id=review_item_id,
        staged_by=staged_by,
        staged_by_email=staged_by_email,
        decision_type=decision_type,
        decision_value=decision_value,
        notes=notes,
        source_url=source_url,
    )


def unstage_review_decision(
    session: Session,
    *,
    review_item_id: uuid.UUID,
    staged_by: uuid.UUID,
) -> ReviewStageResult:
    review_item = session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise ValueError(f"Review item {review_item_id} does not exist.")
    if review_item.state in {REVIEW_ITEM_STATE_COMMITTED, REVIEW_ITEM_STATE_INVALIDATED}:
        raise ValueError(f"Review item {review_item_id} is {review_item.state}, not staged.")

    decision = _active_staged_decision(session, review_item_id)
    if decision is None:
        raise ValueError(f"Review item {review_item_id} has no staged decision.")
    if not _can_modify_staged_decision(decision, staged_by):
        raise ReviewItemAlreadyStagedError(
            review_item_id=review_item.id,
            staged_by=decision.staged_by,
            staged_by_email=decision.staged_by_email,
            decision_type=decision.decision_type,
            staged_at=decision.staged_at,
        )

    result = ReviewStageResult(
        review_item_id=review_item.id,
        decision_id=decision.id,
        decision_type=decision.decision_type or DECISION_DEFER,
        item_state=REVIEW_ITEM_STATE_OPEN,
        staged_by=decision.staged_by,
        staged_by_email=decision.staged_by_email,
        revised=True,
    )
    session.delete(decision)
    review_item.state = REVIEW_ITEM_STATE_OPEN
    review_item.status = ReviewItemStatus.OPEN
    review_item.resolved_by = None
    review_item.resolved_at = None
    session.flush()
    return result


def commit_staged_decisions(
    session: Session,
    *,
    committed_by: uuid.UUID,
    committed_by_email: str | None,
    jurisdiction_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> ReviewCommitResult:
    decisions = _staged_commit_candidates(
        session,
        committed_by=committed_by,
        jurisdiction_id=jurisdiction_id,
    )
    deferred_items = _deferred_item_count(
        session,
        staged_by=committed_by,
        jurisdiction_id=jurisdiction_id,
    )
    affected_project_ids = {
        decision.review_item.project_id
        for decision in decisions
        if decision.review_item.project_id is not None
    }
    touched_jurisdiction_ids = _jurisdiction_ids_for_projects(session, affected_project_ids)
    if dry_run:
        remaining = _remaining_open_item_count(session, jurisdiction_id=jurisdiction_id)
        return ReviewCommitResult(
            committed_decisions=len(decisions),
            affected_projects=len(affected_project_ids),
            review_items_committed=len(decisions),
            review_items_remaining=remaining,
            deferred_items=deferred_items,
            jurisdictions_touched=sorted(touched_jurisdiction_ids, key=str),
            queue_cleared=remaining == 0 and deferred_items == 0,
            dry_run=True,
        )

    with session.begin_nested():
        now = datetime.now(UTC)
        actor = _actor_for_decision(staged_by=committed_by, staged_by_email=committed_by_email)
        field_changes_applied = 0
        committed_item_ids: set[uuid.UUID] = set()
        actual_affected_project_ids: set[uuid.UUID] = set()

        for decision in decisions:
            review_item = decision.review_item
            project_id, field_changes = _apply_staged_decision(
                session,
                decision=decision,
                actor=actor,
                actor_user_id=committed_by,
                actor_email=committed_by_email,
                timestamp=now,
            )
            if project_id is not None:
                actual_affected_project_ids.add(project_id)
            field_changes_applied += field_changes

            decision.state = REVIEW_DECISION_STATE_COMMITTED
            decision.committed_at = now
            decision.committed_by = committed_by
            decision.committed_by_email = committed_by_email
            if decision.staged_by is None:
                decision.staged_by = committed_by
            if decision.staged_by_email is None:
                decision.staged_by_email = committed_by_email
            review_item.state = REVIEW_ITEM_STATE_COMMITTED
            review_item.status = _legacy_status_for_committed_decision(decision)
            review_item.resolved_by = actor[:50]
            review_item.resolved_at = now
            committed_item_ids.add(review_item.id)

        session.flush()
    remaining = _remaining_open_item_count(session, jurisdiction_id=jurisdiction_id)
    deferred_items = _deferred_item_count(
        session,
        staged_by=committed_by,
        jurisdiction_id=jurisdiction_id,
    )
    touched_jurisdiction_ids = _jurisdiction_ids_for_projects(session, actual_affected_project_ids)
    return ReviewCommitResult(
        committed_decisions=len(decisions),
        affected_projects=len(actual_affected_project_ids),
        field_changes_applied=field_changes_applied,
        review_items_committed=len(committed_item_ids),
        review_items_remaining=remaining,
        deferred_items=deferred_items,
        jurisdictions_touched=sorted(touched_jurisdiction_ids, key=str),
        queue_cleared=remaining == 0 and deferred_items == 0,
    )


def _load_open_review_item(session: Session, review_item_id: uuid.UUID) -> ReviewItem:
    review_item = session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise ValueError(f"Review item {review_item_id} does not exist.")
    if review_item.status != ReviewItemStatus.OPEN:
        raise ValueError(
            f"Review item {review_item_id} is {review_item.status.value}, not open."
        )
    if getattr(review_item, "state", REVIEW_ITEM_STATE_OPEN) != REVIEW_ITEM_STATE_OPEN:
        raise ValueError(
            f"Review item {review_item_id} is {review_item.state}, not open."
        )
    return review_item


def _normalize_decision_type(decision_type: str) -> str:
    normalized = decision_type.strip().lower()
    if normalized in STAGEABLE_DECISION_TYPES:
        return normalized
    if normalized.startswith(DECISION_CANDIDATE_PREFIX):
        suffix = normalized.removeprefix(DECISION_CANDIDATE_PREFIX)
        if suffix.isdigit() and int(suffix) >= 0:
            return normalized
    raise ValueError(f"Unsupported review decision type: {decision_type}.")


def _legacy_action_for_decision_type(decision_type: str) -> ReviewDecisionAction:
    if decision_type == DECISION_KEEP_OLD:
        return ReviewDecisionAction.REJECT
    if decision_type == DECISION_CUSTOM:
        return ReviewDecisionAction.OVERRIDE
    if decision_type == DECISION_DEFER:
        return ReviewDecisionAction.DEFER
    return ReviewDecisionAction.ACCEPT


def _actor_for_decision(*, staged_by: uuid.UUID, staged_by_email: str | None) -> str:
    return (staged_by_email or str(staged_by))[:50]


def _load_stageable_review_item(session: Session, review_item_id: uuid.UUID) -> ReviewItem:
    review_item = session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise ValueError(f"Review item {review_item_id} does not exist.")
    if review_item.state in {REVIEW_ITEM_STATE_COMMITTED, REVIEW_ITEM_STATE_INVALIDATED}:
        raise ValueError(f"Review item {review_item_id} is {review_item.state}, not stageable.")
    return review_item


def _active_staged_decision(
    session: Session,
    review_item_id: uuid.UUID,
) -> ReviewDecision | None:
    return session.execute(
        select(ReviewDecision).where(
            ReviewDecision.review_item_id == review_item_id,
            ReviewDecision.state == REVIEW_DECISION_STATE_STAGED,
        )
    ).scalar_one_or_none()


def _can_modify_staged_decision(decision: ReviewDecision, user_id: uuid.UUID) -> bool:
    # Legacy deferred decisions created before Supabase actors are claimable by
    # the first authenticated reviewer who revises or unstages them.
    return decision.staged_by is None or decision.staged_by == user_id


def _staged_commit_candidates(
    session: Session,
    *,
    committed_by: uuid.UUID,
    jurisdiction_id: uuid.UUID | None,
) -> list[ReviewDecision]:
    statement = (
        select(ReviewDecision)
        .join(ReviewDecision.review_item)
        .where(
            ReviewDecision.state == REVIEW_DECISION_STATE_STAGED,
            ReviewDecision.staged_by == committed_by,
            ReviewDecision.decision_type != DECISION_DEFER,
        )
        .order_by(
            ReviewItem.project_id.asc().nulls_last(),
            ReviewDecision.created_at.asc(),
            ReviewDecision.id.asc(),
        )
    )
    if jurisdiction_id is not None:
        statement = statement.join(Project, ReviewItem.project_id == Project.id).where(
            Project.jurisdiction_id == jurisdiction_id
        )
    return session.execute(statement).scalars().all()


def _deferred_item_count(
    session: Session,
    *,
    staged_by: uuid.UUID | None = None,
    jurisdiction_id: uuid.UUID | None = None,
) -> int:
    statement = (
        select(func.count())
        .select_from(ReviewDecision)
        .join(ReviewDecision.review_item)
        .where(
            ReviewDecision.state == REVIEW_DECISION_STATE_STAGED,
            ReviewDecision.decision_type == DECISION_DEFER,
        )
    )
    if staged_by is not None:
        statement = statement.where(
            or_(ReviewDecision.staged_by.is_(None), ReviewDecision.staged_by == staged_by)
        )
    if jurisdiction_id is not None:
        statement = statement.join(Project, ReviewItem.project_id == Project.id).where(
            Project.jurisdiction_id == jurisdiction_id
        )
    return int(session.execute(statement).scalar_one())


def _remaining_open_item_count(
    session: Session,
    *,
    jurisdiction_id: uuid.UUID | None = None,
) -> int:
    statement = (
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.state == REVIEW_ITEM_STATE_OPEN)
    )
    if jurisdiction_id is not None:
        statement = statement.join(Project, ReviewItem.project_id == Project.id).where(
            Project.jurisdiction_id == jurisdiction_id
        )
    return int(session.execute(statement).scalar_one())


def _jurisdiction_ids_for_projects(
    session: Session,
    project_ids: set[uuid.UUID | None],
) -> set[uuid.UUID]:
    normalized_ids = {project_id for project_id in project_ids if project_id is not None}
    if not normalized_ids:
        return set()
    rows = session.execute(
        select(Project.jurisdiction_id).where(Project.id.in_(normalized_ids))
    ).scalars()
    return {jurisdiction_id for jurisdiction_id in rows if jurisdiction_id is not None}


def _legacy_status_for_committed_decision(decision: ReviewDecision) -> ReviewItemStatus:
    decision_type = decision.decision_type or ""
    if decision_type == DECISION_KEEP_OLD:
        return ReviewItemStatus.REJECTED
    return ReviewItemStatus.ACCEPTED


def _apply_staged_decision(
    session: Session,
    *,
    decision: ReviewDecision,
    actor: str,
    actor_user_id: uuid.UUID,
    actor_email: str | None,
    timestamp: datetime,
) -> tuple[uuid.UUID | None, int]:
    review_item = decision.review_item
    if review_item.state != REVIEW_ITEM_STATE_STAGED:
        raise ValueError(f"Review item {review_item.id} is {review_item.state}, not staged.")

    decision_type = _normalize_decision_type(decision.decision_type or "")
    if review_item.item_type in DISCOVERY_REVIEW_ITEM_TYPES:
        if decision_type == DECISION_ACCEPT_NEW:
            if _decision_requests_create_new(decision):
                return _apply_discovery_accept_new_project(
                    session,
                    decision=decision,
                    actor=actor,
                    actor_user_id=actor_user_id,
                    actor_email=actor_email,
                    timestamp=timestamp,
                )
            return _apply_discovery_accept_existing(
                session,
                decision=decision,
                actor=actor,
                actor_user_id=actor_user_id,
                actor_email=actor_email,
                timestamp=timestamp,
            )
        if decision_type == DECISION_KEEP_OLD:
            return _apply_discovery_reject(
                session,
                decision=decision,
                review_item=review_item,
                actor=actor,
                notes=decision.decision_notes or decision.notes,
            )
        raise ValueError(
            f"{decision_type} is not supported for discovery review items in C.h."
        )

    return _apply_field_or_contradiction_decision(
        session,
        decision=decision,
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        timestamp=timestamp,
    )


def _apply_discovery_accept_new_project(
    session: Session,
    *,
    decision: ReviewDecision,
    actor: str,
    actor_user_id: uuid.UUID,
    actor_email: str | None,
    timestamp: datetime,
) -> tuple[uuid.UUID | None, int]:
    review_item = decision.review_item
    source_run = _load_source_run(review_item)
    payload = _payload_mapping(review_item.payload)
    source_record_id = _required_payload_text(payload, "source_record_id")
    source_name = source_run.source_name
    source_type = get_logical_source_type(source_name)
    project = _build_project_from_review_item(
        review_item=review_item,
        source_run=source_run,
        actor=actor,
        new_project_data=_new_project_data_from_decision(decision),
    )
    session.add(project)
    session.flush()
    previous_values = _capture_project_values(project)
    previous_snapshot = snapshot_project_for_diff(project)
    evidence_link = _link_orphan_evidence(
        session,
        project_id=project.id,
        source_type=source_type,
        source_record_id=source_record_id,
    )
    _upsert_project_source_record(
        session,
        project=project,
        review_item=review_item,
        source_name=source_name,
        source_record_id=source_record_id,
        evidence_rows=evidence_link.evidence_rows,
    )
    _persist_review_identifiers(session, project=project, payload=payload)
    project.last_reviewed_by = actor
    project.last_reviewed_date = timestamp.date()
    session.flush()
    resolution_result = resolve_project(
        project.id,
        session,
        apply=True,
        write_resolution_log=True,
    )
    change_log_count = _write_accept_change_logs(
        session,
        project=project,
        review_item=review_item,
        source_name=source_name,
        actor=actor,
        previous_values=previous_values,
        resolution_result=resolution_result,
        timestamp=timestamp,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
    )
    _create_follow_up_review_item(
        session,
        project=project,
        review_item=review_item,
        previous_snapshot=previous_snapshot,
        resolution_result=resolution_result,
    )
    review_item.project_id = project.id
    return project.id, change_log_count


def _apply_discovery_accept_existing(
    session: Session,
    *,
    decision: ReviewDecision,
    actor: str,
    actor_user_id: uuid.UUID,
    actor_email: str | None,
    timestamp: datetime,
) -> tuple[uuid.UUID | None, int]:
    review_item = decision.review_item
    target_project_id = _target_project_id_from_decision(decision)
    if target_project_id is None:
        raise ValueError("Discovery accept_new decisions must include target project_id.")

    source_run = _load_source_run(review_item)
    payload = _payload_mapping(review_item.payload)
    source_record_id = _required_payload_text(payload, "source_record_id")
    source_name = source_run.source_name
    source_type = get_logical_source_type(source_name)
    project = session.get(Project, target_project_id)
    if project is None:
        raise ValueError(f"Project {target_project_id} does not exist.")
    _validate_possible_match_choice(review_item, project.id)

    previous_values = _capture_project_values(project)
    previous_snapshot = snapshot_project_for_diff(project)
    evidence_link = _link_orphan_evidence(
        session,
        project_id=project.id,
        source_type=source_type,
        source_record_id=source_record_id,
    )
    _upsert_project_source_record(
        session,
        project=project,
        review_item=review_item,
        source_name=source_name,
        source_record_id=source_record_id,
        evidence_rows=evidence_link.evidence_rows,
    )
    _persist_review_identifiers(session, project=project, payload=payload)
    project.last_reviewed_by = actor
    project.last_reviewed_date = timestamp.date()
    session.flush()
    resolution_result = resolve_project(
        project.id,
        session,
        apply=True,
        write_resolution_log=True,
    )
    change_log_count = _write_accept_change_logs(
        session,
        project=project,
        review_item=review_item,
        source_name=source_name,
        actor=actor,
        previous_values=previous_values,
        resolution_result=resolution_result,
        timestamp=timestamp,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
    )
    _create_follow_up_review_item(
        session,
        project=project,
        review_item=review_item,
        previous_snapshot=previous_snapshot,
        resolution_result=resolution_result,
    )
    review_item.project_id = project.id
    return project.id, change_log_count


def _decision_requests_create_new(decision: ReviewDecision) -> bool:
    value = decision.decision_value
    return isinstance(value, Mapping) and value.get("create_new") is True


def _new_project_data_from_decision(decision: ReviewDecision) -> Mapping[str, Any] | None:
    value = decision.decision_value
    if isinstance(value, Mapping):
        nested = value.get("new_project_data")
        if isinstance(nested, Mapping):
            return nested
    return None


def _target_project_id_from_decision(decision: ReviewDecision) -> uuid.UUID | None:
    value = decision.decision_value
    if isinstance(value, Mapping):
        raw_project_id = value.get("project_id") or value.get("target_project_id")
    else:
        raw_project_id = value
    if raw_project_id is None:
        return None
    try:
        return uuid.UUID(str(raw_project_id))
    except ValueError as exc:
        raise ValueError(f"Invalid target project_id: {raw_project_id}.") from exc


def _apply_discovery_reject(
    session: Session,
    *,
    decision: ReviewDecision,
    review_item: ReviewItem,
    actor: str,
    notes: str | None,
) -> tuple[uuid.UUID | None, int]:
    source_run = review_item.source_run
    if source_run is None:
        return review_item.project_id, 0
    payload = _payload_mapping(review_item.payload)
    source_record_id = _required_payload_text(payload, "source_record_id")
    dismissed = _find_dismissed_record(
        session,
        source_name=source_run.source_name,
        source_record_id=source_record_id,
    )
    if dismissed is None:
        session.add(
            DismissedRecord(
                source=source_run.source_name,
                source_record_id=source_record_id,
                canonical_address=_optional_payload_text(payload, "canonical_address"),
                reason=_dismiss_reason_from_decision(decision),
                dismissed_by=actor,
                notes=notes,
            )
        )
    return review_item.project_id, 0


def _dismiss_reason_from_decision(decision: ReviewDecision) -> DismissReason:
    value = decision.decision_value
    if isinstance(value, Mapping):
        raw_reason = value.get("reason") or value.get("dismiss_reason")
    else:
        raw_reason = value
    if raw_reason is None:
        return DismissReason.OTHER
    reason = str(raw_reason).strip().lower()
    try:
        return DismissReason(reason)
    except ValueError as exc:
        allowed = ", ".join(reason.value for reason in DismissReason)
        raise ValueError(f"Dismiss reason must be one of: {allowed}.") from exc


def _apply_field_or_contradiction_decision(
    session: Session,
    *,
    decision: ReviewDecision,
    actor: str,
    actor_user_id: uuid.UUID,
    actor_email: str | None,
    timestamp: datetime,
) -> tuple[uuid.UUID | None, int]:
    review_item = decision.review_item
    project = review_item.project
    if project is None:
        raise ValueError(f"Review item {review_item.id} is not linked to a project.")

    decision_type = _normalize_decision_type(decision.decision_type or "")
    field_name = _field_name_for_decision(review_item, decision)
    previous_values = _capture_project_values(project)

    if decision_type == DECISION_ACCEPT_NEW:
        if review_item.item_type == ReviewItemType.OVERRIDE_CONTRADICTION:
            clear_researcher_override_fields(
                session,
                project,
                {field_name},
                cleared_by_user_id=actor_user_id,
                cleared_at=timestamp,
            )
    elif decision_type == DECISION_KEEP_OLD:
        _upsert_review_override(
            session,
            project=project,
            field_name=field_name,
            value=_keep_old_value_for_decision(review_item),
            actor=actor,
            actor_user_id=actor_user_id,
            timestamp=timestamp,
            note=decision.decision_notes or decision.notes,
            source_url=decision.source_url,
        )
    elif decision_type == DECISION_CUSTOM:
        _upsert_review_override(
            session,
            project=project,
            field_name=field_name,
            value=_custom_value_for_decision(decision),
            actor=actor,
            actor_user_id=actor_user_id,
            timestamp=timestamp,
            note=decision.decision_notes or decision.notes,
            source_url=decision.source_url,
        )
    elif decision_type.startswith(DECISION_CANDIDATE_PREFIX):
        _upsert_review_override(
            session,
            project=project,
            field_name=field_name,
            value=_candidate_value_for_decision(review_item, decision_type),
            actor=actor,
            actor_user_id=actor_user_id,
            timestamp=timestamp,
            note=decision.decision_notes or decision.notes,
            source_url=decision.source_url,
        )
    else:
        raise ValueError(f"{decision_type} cannot be committed.")

    project.last_reviewed_by = actor
    project.last_reviewed_date = timestamp.date()
    session.flush()
    resolution_result = resolve_project(
        project.id,
        session,
        apply=True,
        write_resolution_log=True,
        skip_contradiction_review_item_ids=(
            {review_item.id}
            if review_item.item_type == ReviewItemType.OVERRIDE_CONTRADICTION
            else None
        ),
    )
    change_log_count = _write_commit_change_logs(
        session,
        decision=decision,
        project=project,
        review_item=review_item,
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        previous_values=previous_values,
        resolution_result=resolution_result,
        timestamp=timestamp,
    )
    return project.id, change_log_count


def _field_name_for_decision(review_item: ReviewItem, decision: ReviewDecision) -> str:
    if review_item.field_name:
        return review_item.field_name
    payload = _payload_mapping(review_item.payload)
    field_name = _coerce_text(payload.get("field_name"))
    if field_name is not None:
        return field_name
    value = decision.decision_value
    if isinstance(value, Mapping):
        field_name = _coerce_text(value.get("field_name"))
        if field_name is not None:
            return field_name
    for change in payload.get("changes", []):
        if isinstance(change, Mapping):
            field_name = _coerce_text(change.get("field") or change.get("field_name"))
            if field_name is not None:
                return field_name
    if _payload_mapping(payload.get("status_suggestion")):
        return "pipeline_status"
    raise ValueError(f"Review item {review_item.id} does not identify a field.")


def _keep_old_value_for_decision(review_item: ReviewItem) -> Any:
    payload = _payload_mapping(review_item.payload)
    override_payload = _payload_mapping(payload.get("current_override"))
    if "value" in override_payload:
        return override_payload.get("value")
    if "current_value" in payload:
        return payload.get("current_value")
    status_payload = _payload_mapping(payload.get("status_suggestion"))
    if "current_status" in status_payload:
        return status_payload.get("current_status")
    for change in payload.get("changes", []):
        if isinstance(change, Mapping) and "old_value" in change:
            return change.get("old_value")
    raise ValueError(f"Review item {review_item.id} does not provide a current value.")


def _custom_value_for_decision(decision: ReviewDecision) -> Any:
    value = decision.decision_value
    if isinstance(value, Mapping) and "value" in value:
        return value.get("value")
    return value


def _candidate_value_for_decision(review_item: ReviewItem, decision_type: str) -> Any:
    raw_index = int(decision_type.removeprefix(DECISION_CANDIDATE_PREFIX))
    candidate_index = raw_index - 1 if raw_index > 0 else 0
    payload = _payload_mapping(review_item.payload)
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidate_index < len(candidates):
        candidate = candidates[candidate_index]
    elif candidate_index == 0 and isinstance(payload.get("candidate"), Mapping):
        candidate = payload["candidate"]
    else:
        raise ValueError(f"Review item {review_item.id} does not have {decision_type}.")
    if isinstance(candidate, Mapping) and "value" in candidate:
        return candidate.get("value")
    return candidate


def _upsert_review_override(
    session: Session,
    *,
    project: Project,
    field_name: str,
    value: Any,
    actor: str,
    actor_user_id: uuid.UUID,
    timestamp: datetime,
    note: str | None,
    source_url: str | None,
) -> None:
    pre_override_resolution = resolve_project(
        project.id,
        session,
        apply=False,
        write_resolution_log=False,
    )
    resolution = pre_override_resolution.field_resolutions.get(field_name)
    upsert_researcher_overrides(
        session,
        project,
        {
            field_name: {
                "value": serialize_json(value),
                "set_by": actor,
                "set_at": timestamp.isoformat(),
                "note": _coerce_text(note),
                "source_url": _coerce_text(source_url),
                "mode": "review_protected",
                "baseline": _baseline_for_resolution(resolution),
            }
        },
        set_by_user_id=actor_user_id,
    )


def _load_source_run(review_item: ReviewItem) -> SourceRun:
    source_run = review_item.source_run
    if source_run is None:
        raise ValueError(
            f"Review item {review_item.id} is missing source_run context required for accept."
        )
    return source_run


def _build_project_from_review_item(
    *,
    review_item: ReviewItem,
    source_run: SourceRun,
    actor: str,
    new_project_data: Mapping[str, Any] | None,
) -> Project:
    payload = _payload_mapping(review_item.payload)
    mapped_fields = _payload_mapping(payload.get("mapped_fields"))
    normalized_data = {str(key): value for key, value in (new_project_data or {}).items()}

    canonical_address = _coerce_text(
        normalized_data.get("canonical_address")
        or payload.get("canonical_address")
        or mapped_fields.get("canonical_address")
    )
    city = _coerce_text(normalized_data.get("city") or mapped_fields.get("city"))
    state = _coerce_text(normalized_data.get("state") or mapped_fields.get("state"))
    county = _coerce_text(normalized_data.get("county") or mapped_fields.get("county"))
    zip_code = _coerce_text(normalized_data.get("zip") or mapped_fields.get("zip"))
    project_name = _coerce_text(
        normalized_data.get("project_name") or mapped_fields.get("project_name")
    )

    missing_fields = [
        field_name
        for field_name, value in (
            ("canonical_address", canonical_address),
            ("city", city),
            ("state", state),
            ("county", county),
        )
        if value is None
    ]
    if missing_fields:
        raise ValueError(
            "Cannot create a new project from this review item without "
            + ", ".join(missing_fields)
            + "."
        )

    raw_addresses = _derive_raw_addresses(payload, canonical_address)
    return Project(
        canonical_address=canonical_address,
        raw_addresses=raw_addresses,
        market=source_run.market,
        city=city,
        state=state,
        county=county,
        zip=zip_code,
        project_name=project_name,
        created_by=actor,
    )


def _derive_raw_addresses(payload: Mapping[str, Any], canonical_address: str) -> list[str]:
    raw_addresses_value = payload.get("raw_addresses")
    if isinstance(raw_addresses_value, list):
        addresses = [_coerce_text(value) for value in raw_addresses_value]
        cleaned = [value for value in addresses if value]
        if cleaned:
            return cleaned

    raw_payload = _payload_mapping(payload.get("raw_payload"))
    candidate_fields = ("address", "address_line_1", "site_address")
    for field_name in candidate_fields:
        address = _coerce_text(raw_payload.get(field_name))
        if address is not None:
            return [address]
    return [canonical_address]


def _validate_possible_match_choice(review_item: ReviewItem, project_id: uuid.UUID) -> None:
    if review_item.item_type != ReviewItemType.POSSIBLE_MATCH:
        return
    payload = _payload_mapping(review_item.payload)
    match_payload = _payload_mapping(payload.get("match"))
    candidate_ids = {
        uuid.UUID(candidate_id)
        for candidate_id in match_payload.get("candidate_project_ids", [])
        if isinstance(candidate_id, str) and candidate_id.strip()
    }
    if candidate_ids and project_id not in candidate_ids:
        raise ValueError(
            f"Project {project_id} is not one of the candidate projects for review item "
            f"{review_item.id}."
        )


def _link_orphan_evidence(
    session: Session,
    *,
    project_id: uuid.UUID,
    source_type: str,
    source_record_id: str,
) -> EvidenceLinkResult:
    evidence_rows = (
        session.execute(
            select(Evidence)
            .where(
                Evidence.source_type == source_type,
                Evidence.source_record_id == source_record_id,
            )
            .order_by(Evidence.collected_at.asc(), Evidence.id.asc())
        )
        .scalars()
        .all()
    )
    conflicting_project_ids = sorted(
        {
            evidence.project_id
            for evidence in evidence_rows
            if evidence.project_id is not None and evidence.project_id != project_id
        },
        key=str,
    )
    if conflicting_project_ids:
        formatted_ids = ", ".join(str(conflicting_id) for conflicting_id in conflicting_project_ids)
        raise ValueError(
            "Cannot accept review item because evidence for "
            f"{source_type}:{source_record_id} is already linked to other project(s): "
            f"{formatted_ids}."
        )

    relevant_rows: list[Evidence] = []
    linked_count = 0
    for evidence in evidence_rows:
        if evidence.project_id is None:
            evidence.project_id = project_id
            linked_count += 1
        relevant_rows.append(evidence)
    return EvidenceLinkResult(evidence_rows=relevant_rows, linked_count=linked_count)


def _upsert_project_source_record(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    source_name: str,
    source_record_id: str,
    evidence_rows: list[Evidence],
) -> tuple[bool, bool]:
    source_record = session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.source_name == source_name,
            ProjectSourceRecord.source_record_id == source_record_id,
        )
    ).scalar_one_or_none()
    if source_record is not None and source_record.project_id != project.id:
        raise ValueError(
            "Cannot accept review item because source record "
            f"{source_name}:{source_record_id} is already linked to project "
            f"{source_record.project_id}."
        )

    payload = _payload_mapping(review_item.payload)
    raw_payload = _payload_mapping(payload.get("raw_payload")) or _latest_raw_data(evidence_rows)
    mapped_fields = _payload_mapping(payload.get("mapped_fields")) or _latest_mapped_fields(
        evidence_rows
    )
    source_row_id = _coerce_text(payload.get("source_row_id")) or _coerce_text(
        raw_payload.get(":id")
    )
    source_created_at = _coerce_datetime(payload.get("source_created_at"))
    source_updated_at = _coerce_datetime(payload.get("source_updated_at")) or _coerce_datetime(
        raw_payload.get(":updated_at")
    )
    source_row_hash = _coerce_text(payload.get("source_row_hash")) or _latest_raw_data_hash(
        evidence_rows
    )
    collected_at_values = [evidence.collected_at for evidence in evidence_rows]
    first_seen_at = min(collected_at_values, default=None)
    last_seen_at = max(collected_at_values, default=None)
    serialized_raw_payload = dict(raw_payload) if raw_payload else None
    serialized_mapped_fields = dict(mapped_fields) if mapped_fields else None
    field_provenance = (
        {field_name: source_name for field_name in serialized_mapped_fields}
        if serialized_mapped_fields
        else None
    )

    if source_record is None:
        session.add(
            ProjectSourceRecord(
                project_id=project.id,
                source_name=source_name,
                source_record_id=source_record_id,
                source_row_id=source_row_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                source_row_hash=source_row_hash,
                first_seen_at=first_seen_at,
                last_seen_at=last_seen_at,
                last_pulled_at=last_seen_at,
                raw_payload=serialized_raw_payload,
                mapped_fields=serialized_mapped_fields,
                field_provenance=field_provenance,
            )
        )
        return True, False

    source_record.project_id = project.id
    if source_row_id is not None:
        source_record.source_row_id = source_row_id
    if source_created_at is not None:
        source_record.source_created_at = source_created_at
    if source_updated_at is not None:
        source_record.source_updated_at = source_updated_at
    if source_row_hash is not None:
        source_record.source_row_hash = source_row_hash
    if first_seen_at is not None:
        source_record.first_seen_at = (
            min(source_record.first_seen_at, first_seen_at)
            if source_record.first_seen_at is not None
            else first_seen_at
        )
    if last_seen_at is not None:
        source_record.last_seen_at = (
            max(source_record.last_seen_at, last_seen_at)
            if source_record.last_seen_at is not None
            else last_seen_at
        )
        source_record.last_pulled_at = (
            max(source_record.last_pulled_at, last_seen_at)
            if source_record.last_pulled_at is not None
            else last_seen_at
        )
    if serialized_raw_payload is not None:
        source_record.raw_payload = serialized_raw_payload
    if serialized_mapped_fields is not None:
        source_record.mapped_fields = serialized_mapped_fields
        source_record.field_provenance = field_provenance
    return False, True


def _persist_review_identifiers(
    session: Session,
    *,
    project: Project,
    payload: Mapping[str, Any],
) -> tuple[int, list[IdentifierConflict]]:
    identifiers = payload.get("identifiers")
    if not isinstance(identifiers, Mapping):
        return 0, []

    inserted = 0
    conflicts: list[IdentifierConflict] = []
    for identifier_type_name, values in identifiers.items():
        identifier_type = _coerce_identifier_type(identifier_type_name)
        if identifier_type is None or not isinstance(values, list):
            continue
        unique_values = sorted(
            {str(value).strip() for value in values if isinstance(value, str) and value.strip()}
        )
        for value in unique_values:
            owner_project_id = session.execute(
                select(ProjectIdentifier.project_id).where(
                    ProjectIdentifier.identifier_type == identifier_type,
                    ProjectIdentifier.value == value,
                )
            ).scalar_one_or_none()
            if owner_project_id is not None:
                if owner_project_id != project.id:
                    conflicts.append(
                        IdentifierConflict(
                            identifier_type=identifier_type,
                            value=value,
                            owner_project_id=owner_project_id,
                        )
                    )
                continue
            session.add(
                ProjectIdentifier(
                    project_id=project.id,
                    identifier_type=identifier_type,
                    value=value,
                )
            )
            inserted += 1
    return inserted, conflicts


def _normalize_field_overrides(
    field_overrides: Mapping[str, Any] | None,
    *,
    actor: str,
    note: str | None,
    now: datetime,
    candidate_resolutions: Mapping[str, FieldResolution] | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(field_overrides, Mapping):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for field_name, payload in field_overrides.items():
        resolution = (
            candidate_resolutions.get(str(field_name))
            if isinstance(candidate_resolutions, Mapping)
            else None
        )
        normalized[str(field_name)] = _build_override_entry(
            raw_override=payload,
            actor=actor,
            note=note,
            now=now,
            candidate_resolution=resolution if isinstance(resolution, FieldResolution) else None,
        )
    return normalized


def _write_accept_change_logs(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    source_name: str,
    actor: str,
    previous_values: Mapping[str, Any],
    resolution_result: ProjectResolutionResult,
    timestamp: datetime,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> int:
    entries_created = 0
    for field_name in resolution_result.changed_fields:
        priority = CHANGELOG_PRIORITY_BY_FIELD.get(field_name)
        if priority is None:
            continue
        session.add(
            ChangeLog(
                project_id=project.id,
                review_item_id=review_item.id,
                timestamp=timestamp,
                source=source_name,
                field=field_name,
                old_value=previous_values.get(field_name),
                new_value=normalize_value_for_project(resolution_result.resolved_values[field_name]),
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=priority,
                reviewed_by=actor,
                reviewed_by_user_id=actor_user_id,
                reviewed_by_email=actor_email,
            )
        )
        entries_created += 1
    return entries_created


def _write_commit_change_logs(
    session: Session,
    *,
    decision: ReviewDecision,
    project: Project,
    review_item: ReviewItem,
    actor: str,
    actor_user_id: uuid.UUID,
    actor_email: str | None,
    previous_values: Mapping[str, Any],
    resolution_result: ProjectResolutionResult,
    timestamp: datetime,
) -> int:
    entries_created = 0
    source = (
        review_item.source_run.source_name
        if review_item.source_run is not None
        else "review_workflow"
    )
    change_type = _change_type_for_committed_decision(decision)
    for field_name in resolution_result.changed_fields:
        priority = CHANGELOG_PRIORITY_BY_FIELD.get(field_name)
        if priority is None:
            continue
        session.add(
            ChangeLog(
                project_id=project.id,
                review_item_id=review_item.id,
                timestamp=timestamp,
                source=source,
                field=field_name,
                old_value=previous_values.get(field_name),
                new_value=normalize_value_for_project(resolution_result.resolved_values[field_name]),
                change_type=change_type,
                priority=priority,
                reviewed_by=actor,
                reviewed_by_user_id=actor_user_id,
                reviewed_by_email=actor_email,
            )
        )
        entries_created += 1
    return entries_created


def _change_type_for_committed_decision(decision: ReviewDecision) -> ChangeType:
    decision_type = decision.decision_type or ""
    if decision_type == DECISION_KEEP_OLD:
        return ChangeType.RESEARCHER_REJECTED
    if decision_type == DECISION_CUSTOM or decision_type.startswith(DECISION_CANDIDATE_PREFIX):
        return ChangeType.RESEARCHER_OVERRIDE
    return ChangeType.RESEARCHER_CONFIRMED


def _create_follow_up_review_item(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    previous_snapshot,
    resolution_result: ProjectResolutionResult,
) -> int:
    if not resolution_result.review_flags:
        return 0

    payload = _payload_mapping(review_item.payload)
    diff_result = diff_project_snapshots(
        previous_snapshot,
        snapshot_project_for_diff(project),
        status_evidence_type=_status_evidence_type_from_resolution(resolution_result),
        status_evidence_date=_status_evidence_date_from_resolution(resolution_result),
        status_reason=_status_reason_from_resolution(resolution_result),
        review_flags=list(resolution_result.review_flags),
    )
    if not diff_result.has_reviewable_changes:
        return 0

    session.add(
        ReviewItem(
            project_id=project.id,
            source_run_id=review_item.source_run_id,
            item_type=ReviewItemType.STATUS_CHANGE,
            status=ReviewItemStatus.OPEN,
            priority=_review_priority(diff_result),
            payload={
                "origin": "post_accept_resolution",
                "source_review_item_id": str(review_item.id),
                "match": payload.get("match"),
                "source_record_id": payload.get("source_record_id"),
                "canonical_address": payload.get("canonical_address") or project.canonical_address,
                "mapped_fields": payload.get("mapped_fields"),
                "changes": [_serialize_change(change) for change in diff_result.field_changes],
                "review_flags": [
                    _serialize_review_flag(review_flag) for review_flag in diff_result.review_flags
                ],
                "status_suggestion": _serialize_status_suggestion(diff_result.status_suggestion),
            },
        )
    )
    return 1


def _build_status_rejection_override(
    session: Session,
    *,
    project: Project,
    review_item: ReviewItem,
    actor: str,
    note: str | None,
    now: datetime,
) -> dict[str, dict[str, Any]] | None:
    payload = _payload_mapping(review_item.payload)
    status_payload = _payload_mapping(payload.get("status_suggestion"))
    suggested_status = (
        _coerce_text(status_payload.get("suggested_status"))
        or _status_change_new_value(payload)
    )
    current_status = (
        _coerce_text(status_payload.get("current_status"))
        or _status_change_old_value(payload)
    )
    if suggested_status is None or current_status is None:
        return None

    resolution_result = resolve_project(
        project.id,
        session,
        apply=False,
        write_resolution_log=False,
    )
    current_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if current_resolution is None:
        return None
    current_candidate = normalize_value_for_project(current_resolution.value)
    if current_candidate != suggested_status:
        return None

    return {
        "pipeline_status": _build_override_entry(
            raw_override={"value": current_status},
            actor=actor,
            note=note,
            now=now,
            candidate_resolution=current_resolution,
        )
    }


def _capture_project_values(project: Project) -> dict[str, Any]:
    return {
        field_name: normalize_value_for_project(getattr(project, field_name))
        for field_name in CHANGELOG_TRACKED_FIELDS
    }


def _review_priority(diff_result: DiffResult) -> Priority:
    if any(review_flag.priority == Priority.HIGH for review_flag in diff_result.review_flags):
        return Priority.HIGH
    if (
        diff_result.status_suggestion is not None
        and diff_result.status_suggestion.priority == Priority.HIGH
    ):
        return Priority.HIGH
    if any(change.priority == Priority.HIGH for change in diff_result.field_changes):
        return Priority.HIGH
    return Priority.MEDIUM


def _find_dismissed_record(
    session: Session,
    *,
    source_name: str,
    source_record_id: str,
) -> DismissedRecord | None:
    return session.execute(
        select(DismissedRecord).where(
            DismissedRecord.source == source_name,
            DismissedRecord.source_record_id == source_record_id,
        )
    ).scalar_one_or_none()


def _build_override_entry(
    *,
    raw_override: Any,
    actor: str,
    note: str | None,
    now: datetime,
    candidate_resolution: FieldResolution | None,
) -> dict[str, Any]:
    if isinstance(raw_override, Mapping) and "value" in raw_override:
        override_value = serialize_json(raw_override.get("value"))
        mode = _coerce_text(raw_override.get("mode")) or "until_newer_evidence"
        baseline = raw_override.get("baseline")
        if not isinstance(baseline, Mapping):
            baseline = _baseline_for_resolution(candidate_resolution)
        return {
            "value": override_value,
            "set_by": _coerce_text(raw_override.get("set_by")) or actor,
            "set_at": _coerce_text(raw_override.get("set_at")) or now.isoformat(),
            "note": _coerce_text(raw_override.get("note")) or note,
            "mode": mode,
            "baseline": baseline,
        }

    return {
        "value": serialize_json(raw_override),
        "set_by": actor,
        "set_at": now.isoformat(),
        "note": note,
        "mode": "until_newer_evidence",
        "baseline": _baseline_for_resolution(candidate_resolution),
    }


def _baseline_for_resolution(resolution: FieldResolution | None) -> dict[str, Any] | None:
    if resolution is None:
        return None
    frontier = resolution.metadata.get("evidence_frontier")
    if not isinstance(frontier, Mapping):
        return None
    return {
        "evidence_date": serialize_json_value(frontier.get("evidence_date")),
        "collected_at": serialize_json_value(frontier.get("collected_at")),
        "source_tier": frontier.get("source_tier"),
        "source_type": frontier.get("source_type"),
        "evidence_ids": [str(evidence_id) for evidence_id in resolution.evidence_ids],
        "rule_applied": resolution.rule_applied,
    }


def _latest_raw_data(evidence_rows: list[Evidence]) -> dict[str, Any]:
    for evidence in reversed(evidence_rows):
        if isinstance(evidence.raw_data, dict):
            return dict(evidence.raw_data)
    return {}


def _latest_mapped_fields(evidence_rows: list[Evidence]) -> dict[str, Any]:
    for evidence in reversed(evidence_rows):
        if not isinstance(evidence.extracted_fields, Mapping):
            continue
        unwrapped = {}
        for field_name, payload in evidence.extracted_fields.items():
            if isinstance(payload, Mapping) and "value" in payload:
                unwrapped[str(field_name)] = payload.get("value")
        if unwrapped:
            return unwrapped
    return {}


def _latest_raw_data_hash(evidence_rows: list[Evidence]) -> str | None:
    for evidence in reversed(evidence_rows):
        if evidence.raw_data_hash:
            return evidence.raw_data_hash
    return None


def _status_evidence_type_from_resolution(resolution_result: ProjectResolutionResult) -> str | None:
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    evidence_type = status_resolution.metadata.get("evidence_type")
    if evidence_type is None:
        return None
    text = str(evidence_type).strip()
    return text or None


def _status_evidence_date_from_resolution(
    resolution_result: ProjectResolutionResult,
) -> date | None:
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    return status_resolution.evidence_date


def _status_reason_from_resolution(resolution_result: ProjectResolutionResult) -> str | None:
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    review_reason = status_resolution.metadata.get("review_reason")
    if review_reason is None:
        return None
    text = str(review_reason).strip()
    return text or None


def _serialize_change(change) -> dict[str, Any]:
    return {
        "field": change.field,
        "old_value": serialize_json_value(change.old_value),
        "new_value": serialize_json_value(change.new_value),
        "priority": change.priority.value,
    }


def _serialize_review_flag(review_flag: ReviewFlag) -> dict[str, Any]:
    return {
        "code": review_flag.code,
        "message": review_flag.message,
        "priority": review_flag.priority.value,
    }


def _serialize_status_suggestion(suggestion) -> dict[str, Any] | None:
    if suggestion is None:
        return None
    return {
        "current_status": (
            suggestion.current_status.value if suggestion.current_status is not None else None
        ),
        "suggested_status": suggestion.suggested_status.value,
        "evidence_type": suggestion.evidence_type,
        "evidence_date": serialize_json_value(suggestion.evidence_date),
        "reason": suggestion.reason,
        "priority": suggestion.priority.value,
        "rule_code": suggestion.rule_code,
        "proof_level": suggestion.proof_level,
    }


def _coerce_identifier_type(value: Any) -> IdentifierType | None:
    try:
        return IdentifierType(str(value))
    except ValueError:
        return None


def _payload_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _required_payload_text(payload: Mapping[str, Any], field_name: str) -> str:
    value = _optional_payload_text(payload, field_name)
    if value is None:
        raise ValueError(f"Review item payload is missing required field '{field_name}'.")
    return value


def _optional_payload_text(payload: Mapping[str, Any], field_name: str) -> str | None:
    return _coerce_text(payload.get(field_name))


def _status_change_old_value(payload: Mapping[str, Any]) -> str | None:
    for change in payload.get("changes", []):
        if isinstance(change, Mapping) and change.get("field") == "pipeline_status":
            return _coerce_text(change.get("old_value"))
    return None


def _status_change_new_value(payload: Mapping[str, Any]) -> str | None:
    for change in payload.get("changes", []):
        if isinstance(change, Mapping) and change.get("field") == "pipeline_status":
            return _coerce_text(change.get("new_value"))
    return None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
