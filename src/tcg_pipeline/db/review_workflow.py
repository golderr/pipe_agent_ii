from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
from tcg_pipeline.resolution import ProjectResolutionResult, resolve_project
from tcg_pipeline.resolution.engine import normalize_value_for_project
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
    evidence_rows = _link_orphan_evidence(
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
        evidence_rows=evidence_rows,
    )
    identifiers_inserted = _persist_review_identifiers(
        session,
        project=project,
        payload=payload,
    )

    normalized_overrides = _normalize_field_overrides(
        field_overrides,
        actor=actor,
        note=notes,
        now=now,
    )
    if normalized_overrides:
        project.researcher_override = _merge_researcher_overrides(
            project.researcher_override,
            normalized_overrides,
        )

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
        linked_evidence_count=len(evidence_rows),
        source_record_created=source_record_created,
        source_record_updated=source_record_updated,
        identifiers_inserted=identifiers_inserted,
        change_log_entries_created=change_log_entries_created,
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

    session.add(
        ReviewDecision(
            review_item_id=review_item.id,
            action=ReviewDecisionAction.REJECT,
            actor=actor,
            notes=notes,
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
) -> list[Evidence]:
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
    for evidence in evidence_rows:
        if evidence.project_id is None:
            evidence.project_id = project_id
    return evidence_rows


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
) -> int:
    identifiers = payload.get("identifiers")
    if not isinstance(identifiers, Mapping):
        return 0

    inserted = 0
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
                continue
            session.add(
                ProjectIdentifier(
                    project_id=project.id,
                    identifier_type=identifier_type,
                    value=value,
                )
            )
            inserted += 1
    return inserted


def _normalize_field_overrides(
    field_overrides: Mapping[str, Any] | None,
    *,
    actor: str,
    note: str | None,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    if not isinstance(field_overrides, Mapping):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for field_name, payload in field_overrides.items():
        if isinstance(payload, Mapping) and "value" in payload:
            normalized[str(field_name)] = {
                "value": serialize_json(payload.get("value")),
                "set_by": _coerce_text(payload.get("set_by")) or actor,
                "set_at": _coerce_text(payload.get("set_at")) or now.isoformat(),
                "note": _coerce_text(payload.get("note")) or note,
            }
            continue
        normalized[str(field_name)] = {
            "value": serialize_json(payload),
            "set_by": actor,
            "set_at": now.isoformat(),
            "note": note,
        }
    return normalized


def _merge_researcher_overrides(
    existing: Any,
    incoming: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for field_name, payload in incoming.items():
        merged[field_name] = payload
    return merged


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


def _capture_project_values(project: Project) -> dict[str, Any]:
    return {
        field_name: normalize_value_for_project(getattr(project, field_name))
        for field_name in CHANGELOG_TRACKED_FIELDS
    }


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
