from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tcg_pipeline.db.models import ReviewItemType
from tcg_pipeline.review.decision_cards import (
    current_value_for_payload,
    evidence_ids_for_payload,
    field_name_for_payload,
    proposed_value_for_payload,
)
from tcg_pipeline.review.field_metadata import field_metadata_for_review

VALUE_CHANGE_ITEM_TYPES = frozenset(
    {
        ReviewItemType.STATUS_CHANGE,
        ReviewItemType.STATUS_REGRESSION_REVIEW,
        ReviewItemType.OVERRIDE_CONTRADICTION,
        ReviewItemType.STATUS_CHANGE.value,
        ReviewItemType.STATUS_REGRESSION_REVIEW.value,
        ReviewItemType.OVERRIDE_CONTRADICTION.value,
    }
)


def value_change_payload_for_review_item(
    review_item: Any,
    *,
    payload: Mapping[str, Any],
    supporting_evidence_ids: list[str] | None = None,
    dissenting_evidence_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    item_type = getattr(review_item, "item_type", None)
    if item_type not in VALUE_CHANGE_ITEM_TYPES:
        return None

    field_name = getattr(review_item, "field_name", None) or field_name_for_payload(
        item_type,
        payload,
    )
    if field_name is None:
        return None
    field_name = str(field_name)
    current_value = current_value_for_payload(payload, field_name)
    evidence_value = proposed_value_for_payload(payload, field_name)
    if current_value is None and evidence_value is None:
        return None

    metadata = field_metadata_for_review(field_name)
    agent_recommended_value = _agent_recommended_value(
        payload,
        current_value=current_value,
        evidence_value=evidence_value,
    )
    default_result_value = _first_present(agent_recommended_value, evidence_value, current_value)
    return {
        "field_name": field_name,
        "field_label": metadata.label,
        "field_type": metadata.field_type,
        "current_value": current_value,
        "evidence_value": evidence_value,
        "agent_recommended_value": agent_recommended_value,
        "default_result_value": default_result_value,
        "constraints": dict(metadata.constraints),
        "supporting_evidence_ids": supporting_evidence_ids
        if supporting_evidence_ids is not None
        else evidence_ids_for_payload(payload),
        "dissenting_evidence_ids": dissenting_evidence_ids or [],
        "human_summary": _text(payload.get("human_summary")),
    }


def _agent_recommended_value(
    payload: Mapping[str, Any],
    *,
    current_value: Any,
    evidence_value: Any,
) -> Any:
    verdict = _mapping(payload.get("agent_recommendation")) or _mapping(
        payload.get("agent_revised_verdict")
    )
    if not verdict:
        return None

    explicit = _first_present(
        verdict.get("recommended_value"),
        verdict.get("recommended_status"),
        verdict.get("recommended_pipeline_status"),
    )
    if explicit is not None:
        return explicit

    decision = _text(verdict.get("decision"))
    if decision in {
        "confirm_regression",
        "recommend_accept_new",
        "accept_new",
    }:
        return _first_present(verdict.get("proposed_status"), evidence_value)
    if decision in {
        "dismiss",
        "recommend_keep_override",
        "keep_override",
        "keep_old",
        "no_change",
    }:
        return current_value
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
