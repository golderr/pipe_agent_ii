from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from tcg_pipeline.db.models import Priority, ReviewItem, ReviewItemStatus, ReviewItemType
from tcg_pipeline.ingesters._common import serialize_json_value

ACTIVE_REVIEW_STATES = ("open", "staged")
DECISION_CARD_ITEM_TYPES = {
    ReviewItemType.STATUS_CHANGE,
    ReviewItemType.OVERRIDE_CONTRADICTION,
}
MAX_UPSERT_ATTEMPTS = 3


def upsert_decision_card_review_item(
    session: Session,
    *,
    project_id: uuid.UUID,
    item_type: ReviewItemType,
    field_name: str,
    priority: Priority,
    payload: Mapping[str, Any],
    proposed_value: Any,
    source_run_id: uuid.UUID | None = None,
    match_confidence: float | None = None,
    winning_evidence_id: uuid.UUID | None = None,
    contradicted_override_id: uuid.UUID | None = None,
    contradiction_priority: str | None = None,
) -> tuple[ReviewItem, bool]:
    """Create or update the active review item for a logical field decision.

    Returns `(item, created)`. If the active item has a different proposed value,
    it is invalidated first and a fresh item is created so staged decisions never
    silently change meaning.
    """

    normalized_payload = _payload_with_card_fields(
        payload,
        field_name=field_name,
        proposed_value=proposed_value,
        evidence_ids=_evidence_ids_from_payload(payload),
    )
    last_integrity_error: IntegrityError | None = None
    for _attempt in range(MAX_UPSERT_ATTEMPTS):
        existing = _active_item_for_field(
            session,
            project_id=project_id,
            item_type=item_type,
            field_name=field_name,
        )
        if existing is not None:
            if proposed_values_match(existing.payload, proposed_value):
                _refresh_decision_card(
                    existing,
                    priority=priority,
                    payload=normalized_payload,
                    source_run_id=source_run_id,
                    match_confidence=match_confidence,
                    winning_evidence_id=winning_evidence_id,
                    contradicted_override_id=contradicted_override_id,
                    contradiction_priority=contradiction_priority,
                )
                return existing, False
            invalidate_decision_card(existing, reason="proposal_changed")

        item = _new_decision_card_review_item(
            project_id=project_id,
            source_run_id=source_run_id,
            item_type=item_type,
            field_name=field_name,
            priority=priority,
            payload=normalized_payload,
            match_confidence=match_confidence,
            winning_evidence_id=winning_evidence_id,
            contradicted_override_id=contradicted_override_id,
            contradiction_priority=contradiction_priority,
        )
        try:
            with session.begin_nested():
                session.add(item)
                session.flush()
            return item, True
        except IntegrityError as exc:
            last_integrity_error = exc

    existing_after_retries = _active_item_for_field(
        session,
        project_id=project_id,
        item_type=item_type,
        field_name=field_name,
    )
    if existing_after_retries is not None and proposed_values_match(
        existing_after_retries.payload,
        proposed_value,
    ):
        _refresh_decision_card(
            existing_after_retries,
            priority=priority,
            payload=normalized_payload,
            source_run_id=source_run_id,
            match_confidence=match_confidence,
            winning_evidence_id=winning_evidence_id,
            contradicted_override_id=contradicted_override_id,
            contradiction_priority=contradiction_priority,
        )
        return existing_after_retries, False
    if last_integrity_error is not None:
        raise last_integrity_error
    msg = "Unable to upsert decision-card review item after retries."
    raise RuntimeError(msg)


def _new_decision_card_review_item(
    *,
    project_id: uuid.UUID,
    source_run_id: uuid.UUID | None,
    item_type: ReviewItemType,
    field_name: str,
    priority: Priority,
    payload: Mapping[str, Any],
    match_confidence: float | None,
    winning_evidence_id: uuid.UUID | None,
    contradicted_override_id: uuid.UUID | None,
    contradiction_priority: str | None,
) -> ReviewItem:
    return ReviewItem(
        project_id=project_id,
        source_run_id=source_run_id,
        item_type=item_type,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=priority,
        match_confidence=match_confidence,
        field_name=field_name,
        winning_evidence_id=winning_evidence_id,
        payload=payload,
        contradicted_override_id=contradicted_override_id,
        contradiction_priority=contradiction_priority,
    )


def invalidate_decision_card(review_item: ReviewItem, *, reason: str) -> None:
    now = datetime.now(UTC)
    payload = dict(review_item.payload) if isinstance(review_item.payload, dict) else {}
    payload["invalidated_at"] = now.isoformat()
    payload["invalidated_reason"] = reason
    review_item.payload = payload
    review_item.state = "invalidated"
    review_item.status = ReviewItemStatus.OPEN
    review_item.resolved_at = now
    review_item.resolved_by = "decision_card_consolidation"
    review_item.updated_at = now
    session = object_session(review_item)
    for decision in list(review_item.decisions):
        if decision.state == "staged" and session is not None:
            session.delete(decision)


def proposed_values_match(payload: Any, proposed_value: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return normalize_comparable(payload.get("proposed_value")) == normalize_comparable(
        proposed_value
    )


def normalize_comparable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): normalize_comparable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_comparable(item) for item in value]
    return value


def field_name_for_payload(
    item_type: ReviewItemType | str,
    payload: Mapping[str, Any],
) -> str | None:
    field_name = _coerce_text(payload.get("field_name"))
    if field_name is not None:
        return field_name
    if _mapping(payload.get("status_suggestion")):
        return "pipeline_status"
    for change in _mapping_list(payload.get("changes")):
        field_name = _coerce_text(change.get("field") or change.get("field_name"))
        if field_name is not None:
            return field_name
    if item_type == ReviewItemType.OVERRIDE_CONTRADICTION or item_type == "override_contradiction":
        return _coerce_text(_mapping(payload.get("current_override")).get("field_name"))
    return None


def proposed_value_for_payload(payload: Mapping[str, Any], field_name: str | None = None) -> Any:
    if "proposed_value" in payload:
        return payload.get("proposed_value")
    candidate = _mapping(payload.get("candidate"))
    if "value" in candidate:
        return candidate.get("value")
    status_suggestion = _mapping(payload.get("status_suggestion"))
    if "suggested_status" in status_suggestion:
        return status_suggestion.get("suggested_status")
    for change in _mapping_list(payload.get("changes")):
        if _change_matches_field(change, field_name) and "new_value" in change:
            return change.get("new_value")
    mapped_fields = _mapping(payload.get("mapped_fields"))
    if field_name is not None and field_name in mapped_fields:
        return mapped_fields.get(field_name)
    return None


def current_value_for_payload(payload: Mapping[str, Any], field_name: str | None = None) -> Any:
    current_override = _mapping(payload.get("current_override"))
    if "value" in current_override:
        return current_override.get("value")
    if "current_value" in payload:
        return payload.get("current_value")
    status_suggestion = _mapping(payload.get("status_suggestion"))
    if "current_status" in status_suggestion:
        return status_suggestion.get("current_status")
    for change in _mapping_list(payload.get("changes")):
        if _change_matches_field(change, field_name) and "old_value" in change:
            return change.get("old_value")
    return None


def evidence_ids_for_payload(payload: Mapping[str, Any]) -> list[str]:
    return _evidence_ids_from_payload(payload)


def _refresh_decision_card(
    review_item: ReviewItem,
    *,
    priority: Priority,
    payload: Mapping[str, Any],
    source_run_id: uuid.UUID | None,
    match_confidence: float | None,
    winning_evidence_id: uuid.UUID | None,
    contradicted_override_id: uuid.UUID | None,
    contradiction_priority: str | None,
) -> None:
    existing_payload = review_item.payload if isinstance(review_item.payload, dict) else {}
    merged_evidence_ids = _merge_evidence_ids(
        _evidence_ids_from_payload(existing_payload),
        _evidence_ids_from_payload(payload),
    )
    updated_payload = dict(payload)
    updated_payload["evidence_ids"] = merged_evidence_ids
    review_item.priority = priority
    review_item.payload = updated_payload
    if source_run_id is not None:
        review_item.source_run_id = source_run_id
    if match_confidence is not None:
        review_item.match_confidence = match_confidence
    if winning_evidence_id is not None:
        review_item.winning_evidence_id = winning_evidence_id
    if contradicted_override_id is not None:
        review_item.contradicted_override_id = contradicted_override_id
    if contradiction_priority is not None:
        review_item.contradiction_priority = contradiction_priority
    review_item.updated_at = datetime.now(UTC)


def _payload_with_card_fields(
    payload: Mapping[str, Any],
    *,
    field_name: str,
    proposed_value: Any,
    evidence_ids: Iterable[Any],
) -> dict[str, Any]:
    normalized_payload = dict(payload)
    normalized_payload["field_name"] = field_name
    normalized_payload["current_value"] = current_value_for_payload(normalized_payload, field_name)
    normalized_payload["proposed_value"] = serialize_json_value(proposed_value)
    normalized_payload["evidence_ids"] = _merge_evidence_ids(evidence_ids)
    return normalized_payload


def _active_item_for_field(
    session: Session,
    *,
    project_id: uuid.UUID,
    item_type: ReviewItemType,
    field_name: str,
) -> ReviewItem | None:
    return session.execute(
        select(ReviewItem)
        .where(
            ReviewItem.project_id == project_id,
            ReviewItem.item_type == item_type,
            ReviewItem.field_name == field_name,
            ReviewItem.state.in_(ACTIVE_REVIEW_STATES),
        )
        .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
    ).scalars().first()


def _evidence_ids_from_payload(payload: Mapping[str, Any]) -> list[str]:
    evidence_ids = list(_string_values(payload.get("evidence_ids")))
    candidate = _mapping(payload.get("candidate"))
    evidence_ids.extend(_string_values(candidate.get("evidence_ids")))
    return _merge_evidence_ids(evidence_ids)


def _change_matches_field(change: Mapping[str, Any], field_name: str | None) -> bool:
    return (
        field_name is None
        or change.get("field") == field_name
        or change.get("field_name") == field_name
    )


def _merge_evidence_ids(*groups: Iterable[Any]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            text = _coerce_text(value)
            if text is None or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _string_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [text for item in value if (text := _coerce_text(item)) is not None]
    text = _coerce_text(value)
    return [text] if text is not None else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
