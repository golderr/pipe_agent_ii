from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.agents.runner import AgentRunResult
from tcg_pipeline.db.models import (
    Evidence,
    PipelineStatus,
    Priority,
    Project,
    ReviewItem,
    ReviewItemType,
)
from tcg_pipeline.ingesters._common import serialize_json_value
from tcg_pipeline.resolution.fields.status import (
    STATUS_FROM_EVIDENCE_TYPE,
    STATUS_PROGRESS_ORDER,
)
from tcg_pipeline.review.decision_cards import upsert_decision_card_review_item
from tcg_pipeline.review.human_summary import payload_with_human_summary
from tcg_pipeline.source_tiers import get_logical_source_type

STRUCTURED_STATUS_REGRESSION_SOURCE_TYPES = frozenset(
    {"ladbs_permit", "pipedream", "costar"}
)
STATUS_REGRESSION_FIELD_NAME = "pipeline_status"


@dataclass(slots=True)
class StructuredStatusRegressionReviewResult:
    created_count: int = 0
    review_item_ids: list[uuid.UUID] = field(default_factory=list)


def status_regression_candidates_for_evidence(
    resolution_result: Any,
    *,
    source_name: str,
    evidence_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    if resolution_result is None or evidence_id is None:
        return []
    source_type = get_logical_source_type(source_name)
    if source_type not in STRUCTURED_STATUS_REGRESSION_SOURCE_TYPES:
        return []
    status_resolution = resolution_result.field_resolutions.get(STATUS_REGRESSION_FIELD_NAME)
    if status_resolution is None:
        return []
    candidates = status_resolution.metadata.get("regression_candidates")
    if not isinstance(candidates, list):
        return []
    evidence_id_text = str(evidence_id)
    matched: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("source_type") != source_type:
            continue
        if candidate.get("terminal_state_dropped") is True:
            continue
        candidate_evidence_ids = {
            str(item) for item in candidate.get("evidence_ids") or [] if item
        }
        if evidence_id_text not in candidate_evidence_ids:
            continue
        matched.append(candidate)
    return matched


def upsert_structured_status_regression_review_items(
    session: Session,
    *,
    project: Project,
    source_name: str,
    source_record_id: str | None,
    mapped_fields: Mapping[str, Any] | None,
    candidates: list[dict[str, Any]],
    source_run_id: uuid.UUID | None = None,
    match_payload: Mapping[str, Any] | None = None,
    match_confidence: float | None = None,
) -> StructuredStatusRegressionReviewResult:
    result = StructuredStatusRegressionReviewResult()
    source_type = get_logical_source_type(source_name)
    if source_type not in STRUCTURED_STATUS_REGRESSION_SOURCE_TYPES or not candidates:
        return result
    for grouped_candidates in _group_status_regression_candidates(candidates):
        current_status = _coerce_pipeline_status(grouped_candidates[0].get("current_status"))
        proposed_status = _coerce_pipeline_status(grouped_candidates[0].get("proposed_status"))
        if current_status is None or proposed_status is None:
            continue
        support_rows = _status_support_evidence_rows(
            session,
            project=project,
            current_status=current_status,
            exclude_evidence_ids=_candidate_evidence_ids(grouped_candidates),
        )
        payload = _structured_status_regression_payload(
            project=project,
            source_name=source_name,
            source_record_id=source_record_id,
            mapped_fields=mapped_fields,
            match_payload=match_payload,
            candidates=grouped_candidates,
            current_status=current_status,
            proposed_status=proposed_status,
            support_rows=support_rows,
        )
        item, created = upsert_decision_card_review_item(
            session,
            project_id=project.id,
            source_run_id=source_run_id,
            item_type=ReviewItemType.STATUS_REGRESSION_REVIEW,
            field_name=STATUS_REGRESSION_FIELD_NAME,
            priority=_structured_status_regression_priority(
                source_type=source_type,
                candidates=grouped_candidates,
            ),
            match_confidence=match_confidence,
            payload=payload,
            proposed_value=proposed_status.value,
            winning_evidence_id=_first_uuid(_candidate_evidence_ids(grouped_candidates)),
        )
        result.review_item_ids.append(item.id)
        if created:
            result.created_count += 1
    return result


def update_status_regression_review_items_with_agent_result(
    session: Session,
    *,
    review_item_ids: list[uuid.UUID],
    agent_result: AgentRunResult | None,
) -> None:
    if not review_item_ids or agent_result is None:
        return
    rows = (
        session.execute(select(ReviewItem).where(ReviewItem.id.in_(review_item_ids)))
        .scalars()
        .all()
    )
    for item in rows:
        payload = item.payload if isinstance(item.payload, dict) else {}
        verdict = serialize_json_value(agent_result.agent_revised_verdict)
        item.payload = payload_with_human_summary(
            {
                **payload,
                "agent_run_id": str(agent_result.agent_run_id),
                "agent_outcome": agent_result.outcome,
                "agent_recommendation": verdict,
                "agent_revised_verdict": verdict,
                "reasoning_trace": agent_result.reasoning_trace,
                "system_recommendation": _status_regression_system_recommendation(
                    agent_result.agent_revised_verdict,
                    fallback=payload.get("system_recommendation"),
                ),
            },
            item_type=ReviewItemType.STATUS_REGRESSION_REVIEW,
            field_name=STATUS_REGRESSION_FIELD_NAME,
            existing_payload=payload,
        )


def link_status_regression_review_items_to_source_run(
    session: Session,
    *,
    review_item_ids: list[uuid.UUID],
    source_run_id: uuid.UUID,
) -> None:
    if not review_item_ids:
        return
    rows = (
        session.execute(select(ReviewItem).where(ReviewItem.id.in_(review_item_ids)))
        .scalars()
        .all()
    )
    for item in rows:
        item.source_run_id = source_run_id


def _status_regression_system_recommendation(
    verdict: dict[str, Any] | None,
    *,
    fallback: Any,
) -> Any:
    if not isinstance(verdict, dict):
        return fallback
    decision = str(verdict.get("decision") or "").strip()
    if decision == "confirm_regression":
        action = "researcher_apply_regression_recommended"
    elif decision == "dismiss":
        action = "researcher_dismiss_regression_recommended"
    elif decision == "defer_to_review":
        action = "researcher_review_required"
    else:
        return fallback
    return {
        "action": action,
        "reason": verdict.get("reason"),
        "confidence": verdict.get("confidence"),
    }


def _group_status_regression_candidates(
    candidates: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        current = str(candidate.get("current_status") or "").strip()
        proposed = str(candidate.get("proposed_status") or "").strip()
        if not current or not proposed:
            continue
        grouped.setdefault((current, proposed), []).append(candidate)
    return list(grouped.values())


def _structured_status_regression_payload(
    *,
    project: Project,
    source_name: str,
    source_record_id: str | None,
    mapped_fields: Mapping[str, Any] | None,
    match_payload: Mapping[str, Any] | None,
    candidates: list[dict[str, Any]],
    current_status: PipelineStatus,
    proposed_status: PipelineStatus,
    support_rows: list[Evidence],
) -> dict[str, Any]:
    candidate_evidence_ids = _candidate_evidence_ids(candidates)
    support_evidence_ids = [row.id for row in support_rows]
    evidence_ids = _dedupe_uuid([*candidate_evidence_ids, *support_evidence_ids])
    source_type = get_logical_source_type(source_name)
    payload = {
        "origin": "status_regression_candidate",
        "field_name": STATUS_REGRESSION_FIELD_NAME,
        "source_name": source_name,
        "source_type": source_type,
        "source_record_id": source_record_id,
        "current_value": current_status.value,
        "proposed_value": proposed_status.value,
        "current_rank": STATUS_PROGRESS_ORDER.get(current_status),
        "proposed_rank": STATUS_PROGRESS_ORDER.get(proposed_status),
        "rank_delta": _max_candidate_int(candidates, "rank_delta"),
        "candidate_evidence_ids": [str(evidence_id) for evidence_id in candidate_evidence_ids],
        "supporting_evidence_ids": [str(evidence_id) for evidence_id in support_evidence_ids],
        "evidence_ids": [str(evidence_id) for evidence_id in evidence_ids],
        "candidates": _serialize_payload({"items": candidates})["items"],
        "agent_recommendation": None,
        "agent_revised_verdict": None,
        "system_recommendation": {
            "action": "keep_current_recommended",
            "reason": (
                f"{_source_label(source_name)} is not sufficient on its own "
                "to move pipeline status backward."
            ),
        },
        "mapped_fields": _serialize_payload(dict(mapped_fields or {})),
        "human_summary": _structured_status_regression_summary(
            source_name=source_name,
            candidates=candidates,
            current_status=current_status,
            proposed_status=proposed_status,
            support_rows=support_rows,
        ),
        "project": {
            "project_id": str(project.id),
            "project_name": project.project_name,
            "canonical_address": project.canonical_address,
        },
    }
    if match_payload is not None:
        payload["match"] = _serialize_payload(dict(match_payload))
    return payload


def _structured_status_regression_summary(
    *,
    source_name: str,
    candidates: list[dict[str, Any]],
    current_status: PipelineStatus,
    proposed_status: PipelineStatus,
    support_rows: list[Evidence],
) -> str:
    source_label = _source_label(source_name)
    candidate_date = _candidate_display_date(candidates)
    source_phrase = _structured_source_phrase(
        source_label=source_label,
        candidate_date=candidate_date,
        candidates=candidates,
    )
    support_sentence = _status_support_sentence(
        support_rows,
        current_status=current_status,
    )
    return (
        f"{source_phrase} lists this project as {proposed_status.value}. "
        f"The tracker is keeping {current_status.value} because {support_sentence}. "
        f"Recommendation: keep {current_status.value} unless {source_label} reflects "
        "a verified pause, correction, or mapping issue."
    )


def _structured_source_phrase(
    *,
    source_label: str,
    candidate_date: str,
    candidates: list[dict[str, Any]],
) -> str:
    """Build a source-specific phrase for the narrative. Names the LADBS permit
    type + number when available (e.g., 'LADBS Bldg-New permit #19010-10000-00001
    from May 13, 2026') so reviewers can tell at a glance what kind of evidence
    triggered the regression card. Falls back to the prior generic phrasing
    when the source-specific fields aren't populated."""
    primary = candidates[0] if candidates else {}
    if source_label == "LADBS":
        permit_type = _text(primary.get("permit_type"))
        permit_number = _text(primary.get("permit_number"))
        status_desc = _text(primary.get("status_desc"))
        descriptor_parts: list[str] = ["LADBS"]
        if permit_type is not None:
            descriptor_parts.append(f"{permit_type} permit")
        else:
            descriptor_parts.append("permit")
        if permit_number is not None:
            descriptor_parts.append(f"#{permit_number}")
        if status_desc is not None:
            descriptor_parts.append(f"(status: {status_desc})")
        if candidate_date:
            descriptor_parts.append(f"from {candidate_date}")
        return " ".join(descriptor_parts)
    if source_label == "CoStar" and candidate_date:
        return f"CoStar upload from {candidate_date}"
    return f"{source_label} {candidate_date} signal".strip()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _structured_status_regression_priority(
    *,
    source_type: str,
    candidates: list[dict[str, Any]],
) -> Priority:
    if source_type == "costar":
        return Priority.LOW
    if source_type == "pipedream":
        # Production Pipedream sync does not reach this branch yet because seed imports
        # skip existing TCG Pipedream IDs. AGENT.5 should route updates through here.
        if _max_candidate_int(candidates, "rank_delta") >= 2:
            return Priority.HIGH
        if _max_candidate_int(candidates, "current_rank") >= STATUS_PROGRESS_ORDER[
            PipelineStatus.PRE_LEASING_PRE_SELLING
        ]:
            return Priority.HIGH
        return Priority.MEDIUM
    return Priority.HIGH


def _status_support_evidence_rows(
    session: Session,
    *,
    project: Project,
    current_status: PipelineStatus,
    exclude_evidence_ids: list[uuid.UUID],
    limit: int = 2,
) -> list[Evidence]:
    excluded = set(exclude_evidence_ids)
    rows = (
        session.execute(
            select(Evidence)
            .where(
                Evidence.project_id == project.id,
                Evidence.superseded_at.is_(None),
            )
            .order_by(Evidence.evidence_date.desc().nullslast(), Evidence.collected_at.desc())
        )
        .scalars()
        .all()
    )
    support_rows: list[Evidence] = []
    for row in rows:
        if row.id in excluded:
            continue
        if _pipeline_status_from_evidence(row) != current_status:
            continue
        support_rows.append(row)
        if len(support_rows) >= limit:
            break
    return support_rows


def _pipeline_status_from_evidence(evidence: Evidence) -> PipelineStatus | None:
    extracted = evidence.extracted_fields or {}
    pipeline_status = _value_from_field_payload(extracted.get(STATUS_REGRESSION_FIELD_NAME))
    if pipeline_status is not None:
        coerced = _coerce_pipeline_status(pipeline_status)
        if coerced is not None:
            return coerced
    evidence_type = _value_from_field_payload(extracted.get("status_evidence_type"))
    if evidence_type is None:
        return None
    return STATUS_FROM_EVIDENCE_TYPE.get(str(evidence_type))


def _value_from_field_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    return payload.get("value")


def _status_support_sentence(
    support_rows: list[Evidence],
    *,
    current_status: PipelineStatus,
) -> str:
    summaries = [_support_evidence_summary(row) for row in support_rows]
    summaries = [summary for summary in summaries if summary]
    if not summaries:
        return (
            "existing higher-ranked evidence and the forward-only status rule support "
            f"{current_status.value}"
        )
    if len(summaries) == 1:
        return f"{summaries[0]} supports {current_status.value}"
    return f"{' and '.join(summaries)} support {current_status.value}"


def _support_evidence_summary(evidence: Evidence) -> str:
    source_label = _source_type_label(evidence.source_type)
    date_label = _display_date(evidence.evidence_date or evidence.collected_at.date())
    evidence_type = _value_from_field_payload(
        (evidence.extracted_fields or {}).get("status_evidence_type")
    )
    if evidence_type == "building_inspection_recorded":
        return f"{source_label} inspection evidence from {date_label}"
    if evidence_type == "certificate_of_occupancy_issued":
        return f"{source_label} CofO evidence from {date_label}"
    if evidence_type == "building_permit_issued":
        return f"{source_label} permit evidence from {date_label}"
    return f"{source_label} evidence from {date_label}"


def _candidate_evidence_ids(candidates: list[dict[str, Any]]) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for candidate in candidates:
        for value in candidate.get("evidence_ids") or []:
            parsed = _uuid_or_none(value)
            if parsed is not None:
                ids.append(parsed)
    return _dedupe_uuid(ids)


def _dedupe_uuid(values: list[uuid.UUID]) -> list[uuid.UUID]:
    deduped: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _first_uuid(values: list[uuid.UUID]) -> uuid.UUID | None:
    return values[0] if values else None


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_pipeline_status(value: Any) -> PipelineStatus | None:
    if isinstance(value, PipelineStatus):
        return value
    if value is None:
        return None
    try:
        return PipelineStatus(str(value))
    except ValueError:
        return None


def _max_candidate_int(candidates: list[dict[str, Any]], key: str) -> int:
    values: list[int] = []
    for candidate in candidates:
        try:
            values.append(int(candidate.get(key)))
        except (TypeError, ValueError):
            continue
    return max(values, default=0)


def _candidate_display_date(candidates: list[dict[str, Any]]) -> str | None:
    dates = sorted(
        (
            _parse_date(candidate.get("evidence_date"))
            for candidate in candidates
            if candidate.get("evidence_date") is not None
        ),
        reverse=True,
    )
    return _display_date(dates[0]) if dates else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _display_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _source_label(source_name: str) -> str:
    if source_name == "costar":
        return "CoStar"
    if source_name == "pipedream":
        return "Pipedream"
    if source_name.startswith("ladbs"):
        return "LADBS"
    return source_name.replace("_", " ").title()


def _source_type_label(source_type: str) -> str:
    if source_type in {"ladbs_inspection", "ladbs_permit", "ladbs_cofo"}:
        return "LADBS"
    if source_type == "news_article":
        return "news"
    return _source_label(source_type)


def _serialize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_json_value(value) for key, value in payload.items()}
