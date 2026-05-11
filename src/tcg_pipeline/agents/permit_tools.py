from __future__ import annotations

import enum
import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import or_, select

from tcg_pipeline.agents.runner import AgentRunRequest
from tcg_pipeline.agents.tools import AgentTool, AgentToolError, AgentToolResult
from tcg_pipeline.db.models import Evidence

LADBS_PERMIT_SOURCE_TYPES = ("ladbs_permit", "ladbs_inspection", "ladbs_cofo")
GET_PERMITS_OUTPUT_TOKEN_BUDGET = 2200
GET_PERMITS_DEFAULT_LIMIT = 10
GET_PERMITS_MAX_LIMIT = 10
LADBS_APN_DIGIT_LENGTH = 10


def handle_get_permits_for_project(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    project_id = _required_uuid(tool_input.get("project_id"), field_name="project_id")
    limit = _bounded_int(
        tool_input.get("limit"),
        default=GET_PERMITS_DEFAULT_LIMIT,
        maximum=GET_PERMITS_MAX_LIMIT,
    )
    if request.session_factory is None:
        raise AgentToolError("Tool get_permits_for_project requires a session_factory.")

    with request.session_factory() as session:
        evidence_rows = (
            session.execute(
                _base_permit_query()
                .where(Evidence.project_id == project_id)
                .limit(limit)
            )
            .scalars()
            .all()
        )

    permits = [_permit_payload(row) for row in evidence_rows]
    payload = {
        "project_id": str(project_id),
        "limit": limit,
        "permits": permits,
        "total_returned": len(permits),
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {len(permits)} LADBS permit evidence rows for project.",
        total_results=len(permits),
    )


def handle_get_permits_for_parcel(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    parcel_id = _required_text(tool_input.get("parcel_id"), field_name="parcel_id")
    lookup_values = _parcel_lookup_values(parcel_id)
    limit = _bounded_int(
        tool_input.get("limit"),
        default=GET_PERMITS_DEFAULT_LIMIT,
        maximum=GET_PERMITS_MAX_LIMIT,
    )
    if request.session_factory is None:
        raise AgentToolError("Tool get_permits_for_parcel requires a session_factory.")

    with request.session_factory() as session:
        evidence_rows = (
            session.execute(
                _base_permit_query()
                .where(
                    or_(
                        Evidence.raw_data["apn"].astext.in_(lookup_values),
                        Evidence.extracted_fields["apn"]["value"].astext.in_(
                            lookup_values
                        ),
                    )
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )

    permits = [_permit_payload(row) for row in evidence_rows]
    payload = {
        "parcel_id": parcel_id,
        "normalized_parcel_id": _normalize_parcel_id(parcel_id),
        "limit": limit,
        "permits": permits,
        "total_returned": len(permits),
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {len(permits)} LADBS permit evidence rows for parcel.",
        total_results=len(permits),
    )


GET_PERMITS_FOR_PROJECT_TOOL = AgentTool(
    name="get_permits_for_project",
    description=(
        "Fetch compact active LADBS permit, inspection, and CofO evidence rows for a known "
        "TCG project_id. Use this to compare a new permit against already-attributed permit "
        "history. Input requires project_id as a UUID. Optional limit defaults to 10 and is "
        "capped at 10."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "UUID of the TCG project to inspect.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_PERMITS_MAX_LIMIT,
                "description": "Maximum permit evidence rows to return. Defaults to 10.",
            },
        },
        "required": ["project_id"],
        "additionalProperties": False,
    },
    output_token_budget=GET_PERMITS_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_permits_for_project,
)

GET_PERMITS_FOR_PARCEL_TOOL = AgentTool(
    name="get_permits_for_parcel",
    description=(
        "Fetch compact active LADBS permit, inspection, and CofO evidence rows whose raw or "
        "mapped APN matches a parcel ID. Use this when the intake row has an APN and project "
        "attribution is uncertain. Input requires parcel_id. Optional limit defaults to 10 "
        "and is capped at 10."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "parcel_id": {
                "type": "string",
                "description": "Assessor parcel number/APN, with or without separators.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_PERMITS_MAX_LIMIT,
                "description": "Maximum permit evidence rows to return. Defaults to 10.",
            },
        },
        "required": ["parcel_id"],
        "additionalProperties": False,
    },
    output_token_budget=GET_PERMITS_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_permits_for_parcel,
)


def _base_permit_query():
    return (
        select(Evidence)
        .where(
            Evidence.source_type.in_(LADBS_PERMIT_SOURCE_TYPES),
            Evidence.superseded_at.is_(None),
        )
        .order_by(
            Evidence.evidence_date.desc().nulls_last(),
            Evidence.collected_at.desc(),
            Evidence.id.desc(),
        )
    )


def _permit_payload(evidence: Evidence) -> dict[str, Any]:
    payload = {
        "evidence_id": str(evidence.id),
        "project_id": _serialize(evidence.project_id),
        "source_type": evidence.source_type,
        "source_record_id": evidence.source_record_id,
        "evidence_date": _serialize(evidence.evidence_date),
        "collected_at": _serialize(evidence.collected_at),
        "permit_number": _first_value(evidence, "permit_number", "pcis_permit", "permit_nbr")
        or evidence.source_record_id,
        "apn": _first_value(evidence, "apn"),
        "permit_type": _first_value(evidence, "permit_type"),
        "permit_sub_type": _first_value(evidence, "permit_sub_type"),
        "status_evidence_type": _first_value(evidence, "status_evidence_type"),
        "status_desc": _first_value(evidence, "status_desc", "latest_status"),
        "issue_date": _first_value(evidence, "permit_issue_date", "issue_date"),
        "inspection_date": _first_value(evidence, "inspection_date"),
        "cofo_issue_date": _first_value(evidence, "cofo_issue_date"),
        "description": _compact_text(
            _first_value(evidence, "description", "work_desc"),
            max_chars=360,
        ),
        "valuation": _first_value(evidence, "valuation"),
        "applicant": _first_value(evidence, "applicant", "applicant_business_name"),
        "total_units": _first_value(evidence, "total_units", "of_residential_dwelling_units"),
        "notes": _compact_text(evidence.notes, max_chars=180),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _first_value(evidence: Evidence, *field_names: str) -> Any:
    extracted_fields = evidence.extracted_fields or {}
    raw_data = evidence.raw_data or {}
    for field_name in field_names:
        extracted = extracted_fields.get(field_name)
        if isinstance(extracted, dict):
            value = extracted.get("value")
            if value not in (None, ""):
                return _serialize(value)
        value = raw_data.get(field_name)
        if value not in (None, ""):
            return _serialize(value)
    return None


def _required_uuid(value: Any, *, field_name: str) -> uuid.UUID:
    if value in (None, ""):
        raise AgentToolError(f"Tool requires {field_name}.")
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AgentToolError(f"Tool requires a valid {field_name}.") from exc


def _required_text(value: Any, *, field_name: str) -> str:
    text_value = str(value or "").strip()
    if not text_value:
        raise AgentToolError(f"Tool requires {field_name}.")
    return text_value


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentToolError("Tool integer parameter must be a valid integer.") from exc
    return max(1, min(parsed, maximum))


def _parcel_lookup_values(parcel_id: str) -> list[str]:
    values = {parcel_id.strip()}
    normalized = _normalize_parcel_id(parcel_id)
    if normalized is not None:
        values.add(normalized)
    return sorted(value for value in values if value)


def _normalize_parcel_id(parcel_id: str) -> str | None:
    # LADBS currently uses LA County 10-digit APNs; future non-LADBS permit
    # profiles should own their own parcel normalizer.
    digits = re.sub(r"[^\d]", "", parcel_id)
    return digits if len(digits) == LADBS_APN_DIGIT_LENGTH else None


def _compact_text(value: Any, *, max_chars: int = 240) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    compact = re.sub(r"\s+", " ", text_value)
    if len(compact) <= max_chars:
        return compact
    return compact[: max(max_chars - 3, 0)].rstrip() + "..."


def _serialize(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize(item) for item in value]
    return value
