from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
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
from tcg_pipeline.db.researcher_overrides import upsert_researcher_overrides
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
        )
    )
    review_item.status = ReviewItemStatus.ACCEPTED
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
        )
    )
    review_item.status = ReviewItemStatus.REJECTED
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
        )
    )
    review_item.status = ReviewItemStatus.DEFERRED
    review_item.resolved_by = actor
    review_item.resolved_at = now
    return ReviewWorkflowResult(
        review_item_id=review_item.id,
        action=ReviewDecisionAction.DEFER,
        project_id=review_item.project_id,
    )


def _load_open_review_item(session: Session, review_item_id: uuid.UUID) -> ReviewItem:
    review_item = session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise ValueError(f"Review item {review_item_id} does not exist.")
    if review_item.status != ReviewItemStatus.OPEN:
        raise ValueError(
            f"Review item {review_item_id} is {review_item.status.value}, not open."
        )
    return review_item


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
            )
        )
        entries_created += 1
    return entries_created


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
