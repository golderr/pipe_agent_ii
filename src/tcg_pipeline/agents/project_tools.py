from __future__ import annotations

import enum
import uuid
from collections import Counter
from datetime import date, datetime
from typing import Any

from sqlalchemy import select, text

from tcg_pipeline.agents.runner import AgentRunRequest
from tcg_pipeline.agents.tools import AgentTool, AgentToolError, AgentToolResult
from tcg_pipeline.db.models import Evidence, Project

GET_PROJECT_STATE_OUTPUT_TOKEN_BUDGET = 1500

PROJECT_STATE_SNAPSHOT_FIELDS = (
    "project_name",
    "canonical_address",
    "raw_addresses",
    "lat",
    "lng",
    "city",
    "state",
    "county",
    "market",
    "jurisdiction",
    "developer",
    "pipeline_status",
    "product_type",
    "age_restriction",
    "rent_or_sale",
    "total_units",
    "market_rate_units",
    "affordable_units",
    "stories",
    "retail_sf",
    "office_sf",
    "hotel_keys",
    "date_delivery",
    "date_construction_start",
    "last_evidence_date",
    "status_source",
)

PROJECT_FIELD_RESOLUTION_SQL = text(
    """
    SELECT
        field,
        current_value,
        resolved_value,
        evidence_ids,
        rule_applied,
        confidence,
        created_at
    FROM project_field_resolution
    WHERE project_id = CAST(:project_id AS uuid)
    ORDER BY field
    """
)

PROJECT_LATEST_EVIDENCE_SQL = text(
    """
    SELECT
        evidence_id,
        source_type,
        collected_at,
        evidence_date,
        notes
    FROM project_latest_evidence
    WHERE project_id = CAST(:project_id AS uuid)
    """
)


def handle_get_project_state(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    project_id = _required_uuid(tool_input.get("project_id"), field_name="project_id")
    if request.session_factory is None:
        raise AgentToolError("Tool get_project_state requires a session_factory.")

    with request.session_factory() as session:
        project = session.get(Project, project_id)
        if project is None:
            return AgentToolResult(
                payload={"project_id": str(project_id), "found": False},
                summary=f"Project {project_id} was not found.",
                total_results=0,
            )

        field_rows = [
            dict(row)
            for row in session.execute(
                PROJECT_FIELD_RESOLUTION_SQL,
                {"project_id": str(project_id)},
            ).mappings()
        ]
        evidence_metadata = _evidence_metadata_for_field_rows(session, field_rows)
        fields = [
            _field_resolution_payload(row, evidence_metadata=evidence_metadata)
            for row in field_rows
        ]
        latest_evidence = session.execute(
            PROJECT_LATEST_EVIDENCE_SQL,
            {"project_id": str(project_id)},
        ).mappings().first()

    payload = {
        "project": _project_payload(project),
        "latest_evidence": _latest_evidence_payload(latest_evidence),
        "fields": fields,
        "confidence_breakdown": dict(
            Counter(str(field.get("confidence") or "unknown") for field in fields)
        ),
        "field_count": len(fields),
    }
    return AgentToolResult(
        payload=payload,
        summary=_project_state_summary(payload),
        total_results=len(fields),
    )


GET_PROJECT_STATE_TOOL = AgentTool(
    name="get_project_state",
    description=(
        "Fetch the current TCG project snapshot and field-level resolution provenance for a "
        "known project_id. Call this before deciding whether a new source agrees with, "
        "updates, or contradicts an existing project. Input must include project_id as a UUID. "
        "Output includes compact project fields, latest evidence, resolved field values, "
        "confidence, rule labels, and evidence IDs; it does not return raw evidence bodies."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "UUID of the project to inspect.",
            }
        },
        "required": ["project_id"],
        "additionalProperties": False,
    },
    output_token_budget=GET_PROJECT_STATE_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_project_state,
)


def _required_uuid(value: Any, *, field_name: str) -> uuid.UUID:
    if value in (None, ""):
        raise AgentToolError(f"Tool get_project_state requires {field_name}.")
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AgentToolError(f"Tool get_project_state requires a valid {field_name}.") from exc


def _evidence_metadata_for_field_rows(
    session,
    field_rows: list[dict[str, Any]],
) -> dict[uuid.UUID, dict[str, Any]]:
    evidence_ids = sorted(
        {
            evidence_id
            for row in field_rows
            for evidence_id in _uuid_list(row.get("evidence_ids"))
        },
        key=str,
    )
    if not evidence_ids:
        return {}
    rows = session.execute(
        select(
            Evidence.id,
            Evidence.source_type,
            Evidence.evidence_date,
            Evidence.collected_at,
        ).where(Evidence.id.in_(evidence_ids))
    ).all()
    return {
        row.id: {
            "source_type": row.source_type,
            "evidence_date": _serialize(row.evidence_date),
            "collected_at": _serialize(row.collected_at),
        }
        for row in rows
    }


def _field_resolution_payload(
    row: dict[str, Any],
    *,
    evidence_metadata: dict[uuid.UUID, dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = _uuid_list(row.get("evidence_ids"))
    evidence_rows = [
        {"evidence_id": str(evidence_id), **evidence_metadata[evidence_id]}
        for evidence_id in evidence_ids
        if evidence_id in evidence_metadata
    ]
    return {
        "field_name": str(row.get("field")),
        "value": _serialize(row.get("resolved_value")),
        "current_value": _serialize(row.get("current_value")),
        "rule": row.get("rule_applied"),
        "confidence": _serialize(row.get("confidence")),
        "updated_at": _serialize(row.get("created_at")),
        "evidence_ids": [str(evidence_id) for evidence_id in evidence_ids],
        "evidence": evidence_rows,
    }


def _project_payload(project: Project) -> dict[str, Any]:
    payload = {"project_id": str(project.id), "found": True}
    for field_name in PROJECT_STATE_SNAPSHOT_FIELDS:
        payload[field_name] = _serialize(getattr(project, field_name))
    return payload


def _latest_evidence_payload(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "evidence_id": str(row["evidence_id"]),
        "source_type": row["source_type"],
        "collected_at": _serialize(row["collected_at"]),
        "evidence_date": _serialize(row["evidence_date"]),
        "notes": row["notes"],
    }


def _project_state_summary(payload: dict[str, Any]) -> str:
    project = payload["project"]
    name = project.get("project_name") or project.get("canonical_address") or project["project_id"]
    latest = payload.get("latest_evidence") or {}
    source = latest.get("source_type") or "no latest evidence"
    return f"{name}: {payload['field_count']} resolved fields; latest evidence {source}."


def _uuid_list(value: Any) -> list[uuid.UUID]:
    if not value:
        return []
    result: list[uuid.UUID] = []
    for item in value:
        try:
            result.append(item if isinstance(item, uuid.UUID) else uuid.UUID(str(item)))
        except (TypeError, ValueError):
            continue
    return result


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
