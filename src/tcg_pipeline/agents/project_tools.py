from __future__ import annotations

import enum
import uuid
from collections import Counter
from datetime import date, datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select, text

from tcg_pipeline.agents.runner import AgentRunRequest
from tcg_pipeline.agents.tools import AgentTool, AgentToolError, AgentToolResult
from tcg_pipeline.db.models import Evidence, PipelineStatus, Project
from tcg_pipeline.matching.normalizer import normalize_address

GET_PROJECT_STATE_OUTPUT_TOKEN_BUDGET = 1500
SEARCH_PROJECTS_OUTPUT_TOKEN_BUDGET = 1800
SEARCH_PROJECTS_DEFAULT_TOP_K = 5
SEARCH_PROJECTS_MAX_TOP_K = 10
SEARCH_PROJECTS_MIN_SCORE = 0.20
DELETED_PROJECT_STATUSES = {
    PipelineStatus.DELETE_DUPLICATE.value,
    PipelineStatus.DELETE_NOT_RESIDENTIAL.value,
    PipelineStatus.DELETE_OUTSIDE_MARKET_AREA.value,
}

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


def handle_search_projects(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    query_text = _optional_text(tool_input.get("query_text"))
    address = _optional_text(tool_input.get("address"))
    project_name = _optional_text(tool_input.get("project_name"))
    developer = _optional_text(tool_input.get("developer"))
    if not any((query_text, address, project_name, developer)):
        raise AgentToolError(
            "Tool search_projects requires query_text, address, project_name, or developer."
        )
    top_k = _bounded_int(
        tool_input.get("top_k"),
        default=SEARCH_PROJECTS_DEFAULT_TOP_K,
        maximum=SEARCH_PROJECTS_MAX_TOP_K,
    )
    if request.session_factory is None:
        raise AgentToolError("Tool search_projects requires a session_factory.")

    with request.session_factory() as session:
        projects = session.execute(
            select(Project)
            .where(Project.pipeline_status.notin_(sorted(DELETED_PROJECT_STATUSES)))
            .order_by(Project.project_name.asc().nulls_last(), Project.canonical_address.asc())
        ).scalars().all()

    candidates = [
        _project_search_payload(
            project,
            query_text=query_text,
            address=address,
            project_name=project_name,
            developer=developer,
        )
        for project in projects
    ]
    candidates = [
        candidate
        for candidate in candidates
        if candidate["score"] >= SEARCH_PROJECTS_MIN_SCORE
    ]
    candidates.sort(
        key=lambda candidate: (
            -float(candidate["score"]),
            str(candidate.get("project_name") or ""),
            str(candidate["project_id"]),
        )
    )
    matches = candidates[:top_k]
    payload = {
        "query_text": query_text,
        "address": address,
        "project_name": project_name,
        "developer": developer,
        "top_k": top_k,
        "total_available": len(candidates),
        "matches": matches,
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {len(matches)} candidate projects.",
        total_results=len(candidates),
    )


SEARCH_PROJECTS_TOOL = AgentTool(
    name="search_projects",
    description=(
        "Search the TCG project registry by article-observed address, project name, "
        "developer, or a compact query string. Use this when the deterministic matcher "
        "returned new_candidate but the source may refer to an existing TCG project. "
        "If a returned project looks relevant, call get_project_state with its project_id "
        "before promoting the source to that existing project."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "Compact search phrase combining observed name/address/developer.",
            },
            "address": {
                "type": "string",
                "description": "Article-observed project address or street line.",
            },
            "project_name": {
                "type": "string",
                "description": "Article-observed project or marketing name.",
            },
            "developer": {
                "type": "string",
                "description": "Article-observed developer or sponsor name.",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": SEARCH_PROJECTS_MAX_TOP_K,
                "description": "Maximum candidate projects to return. Defaults to 5.",
            },
        },
        "additionalProperties": False,
    },
    output_token_budget=SEARCH_PROJECTS_OUTPUT_TOKEN_BUDGET,
    handler=handle_search_projects,
)


def _required_uuid(value: Any, *, field_name: str) -> uuid.UUID:
    if value in (None, ""):
        raise AgentToolError(f"Tool get_project_state requires {field_name}.")
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AgentToolError(f"Tool get_project_state requires a valid {field_name}.") from exc


def _optional_text(value: Any) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentToolError("Tool search_projects integer parameter must be valid.") from exc
    return max(1, min(parsed, maximum))


def _project_search_payload(
    project: Project,
    *,
    query_text: str | None,
    address: str | None,
    project_name: str | None,
    developer: str | None,
) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    canonical_addresses = _candidate_canonical_addresses(address, query_text)
    if _project_address_matches(project, canonical_addresses):
        score += 0.72
        reasons.append("address_match")
    elif query_text and _text_ratio(query_text, project.canonical_address) >= 92:
        score += 0.45
        reasons.append("address_text_fuzzy")

    name_ratio = _project_name_ratio(project, project_name, query_text)
    if name_ratio >= 95:
        score += 0.22
        reasons.append("project_name_strong")
    elif name_ratio >= 85:
        score += 0.14
        reasons.append("project_name_fuzzy")

    developer_ratio = _text_ratio(developer, project.developer)
    if developer_ratio >= 95:
        score += 0.12
        reasons.append("developer_strong")
    elif developer_ratio >= 88:
        score += 0.07
        reasons.append("developer_fuzzy")

    if query_text and _text_ratio(query_text, project.project_name) >= 90:
        score += 0.08
        reasons.append("query_project_name")
    if query_text and _text_ratio(query_text, project.developer) >= 92:
        score += 0.05
        reasons.append("query_developer")

    return {
        "project_id": str(project.id),
        "score": round(min(score, 1.0), 4),
        "reasons": reasons,
        "project_name": project.project_name,
        "canonical_address": project.canonical_address,
        "developer": project.developer,
        "pipeline_status": _serialize(project.pipeline_status),
        "product_type": _serialize(project.product_type),
        "total_units": project.total_units,
        "affordable_units": project.affordable_units,
        "market_rate_units": project.market_rate_units,
    }


def _candidate_canonical_addresses(*values: str | None) -> set[str]:
    addresses: set[str] = set()
    for value in values:
        text = _optional_text(value)
        if text is None:
            continue
        try:
            normalized = normalize_address(
                text,
                city="Los Angeles",
                state="CA",
                market="los_angeles",
            )
        except Exception:
            continue
        for candidate in (normalized.canonical_address, normalized.canonical_street_line):
            if candidate:
                addresses.add(candidate)
    return addresses


def _project_address_matches(project: Project, canonical_addresses: set[str]) -> bool:
    project_address = _normalized_compare(project.canonical_address)
    for candidate in canonical_addresses:
        normalized_candidate = _normalized_compare(candidate)
        if project_address == normalized_candidate:
            return True
        if project_address.startswith(f"{normalized_candidate} "):
            return True
    return False


def _project_name_ratio(
    project: Project,
    project_name: str | None,
    query_text: str | None,
) -> float:
    names = [project_name, query_text]
    project_names = [project.project_name, *list(project.previous_names or [])]
    scores = [
        _text_ratio(candidate, known_name)
        for candidate in names
        for known_name in project_names
    ]
    return max(scores, default=0.0)


def _text_ratio(left: str | None, right: str | None) -> float:
    left_text = _optional_text(left)
    right_text = _optional_text(right)
    if left_text is None or right_text is None:
        return 0.0
    return float(fuzz.token_set_ratio(left_text, right_text))


def _normalized_compare(value: str) -> str:
    return " ".join(value.upper().replace(".", "").split())


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
