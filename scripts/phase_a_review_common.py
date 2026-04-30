from __future__ import annotations

import csv
import enum
import json
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tcg_pipeline.db.models import Evidence, Project, ResolutionLog  # noqa: E402
from tcg_pipeline.resolution import ProjectResolutionResult, resolve_project  # noqa: E402
from tcg_pipeline.resolution.engine import normalize_value_for_project  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "notes" / "phase_a_review"
DEFAULT_MARKET = "los_angeles"
DEFAULT_ESTIMATE_SAMPLE_SIZE = 10
DEFAULT_ESTIMATE_SAMPLE_SEED = 20260423
DEVELOPER_REVIEW_RULE_ORDER = {
    "most_recent_wins_canonicalized": 0,
    "most_recent_wins": 1,
    "most_recent_wins_canonicalization_review_required": 2,
}
HELIO_CLUSTER_KEY = "helio_ucla"
LIKELY_ALIAS_CANDIDATES = {
    ("Jamison Services", "Jamison Properties"),
    ("Wiseman Development", "Wiseman Residential"),
}


@dataclass(slots=True)
class ProjectReviewContext:
    project: Project
    resolution_result: ProjectResolutionResult
    evidence_count: int
    evidence_by_id: dict[str, Evidence]

    @property
    def last_evidence_date(self) -> Any:
        return self.resolution_result.field_resolutions["last_evidence_date"].value


def load_resolution_log_rows(
    session: Session,
    *,
    market: str,
    field_name: str,
) -> list[tuple[ResolutionLog, Project]]:
    return session.execute(
        select(ResolutionLog, Project)
        .join(Project, Project.id == ResolutionLog.project_id)
        .where(
            Project.market == market,
            ResolutionLog.field == field_name,
        )
        .order_by(Project.canonical_address, ResolutionLog.id)
    ).all()


def load_project_review_context(
    session: Session,
    project_id,
    *,
    cache: dict[str, ProjectReviewContext],
) -> ProjectReviewContext:
    cache_key = str(project_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} does not exist.")

    resolution_result = resolve_project(
        project_id,
        session,
        apply=False,
        write_resolution_log=False,
    )
    evidence_rows = session.execute(
        select(Evidence).where(
            Evidence.project_id == project_id,
            Evidence.superseded_at.is_(None),
        )
    ).scalars().all()
    evidence_by_id = {str(evidence.id): evidence for evidence in evidence_rows}
    context = ProjectReviewContext(
        project=project,
        resolution_result=resolution_result,
        evidence_count=len(evidence_rows),
        evidence_by_id=evidence_by_id,
    )
    cache[cache_key] = context
    return context


def classify_delta_shape(current_value: Any, resolved_value: Any) -> str:
    normalized_current = normalize_value_for_project(current_value)
    normalized_resolved = normalize_value_for_project(resolved_value)
    if normalized_current == normalized_resolved:
        return "unchanged"
    if normalized_current in {None, ""} and normalized_resolved not in {None, ""}:
        return "null_to_value"
    if normalized_current not in {None, ""} and normalized_resolved in {None, ""}:
        return "value_to_null"
    return "value_changed"


def serialize_csv_value(value: Any) -> Any:
    normalized = normalize_value_for_project(value)
    if normalized is None:
        return ""
    if isinstance(normalized, bool):
        return str(normalized).lower()
    if isinstance(normalized, (str, int, float)):
        return normalized
    if isinstance(normalized, (date, datetime)):
        return normalized.isoformat()
    if isinstance(normalized, enum.Enum):
        return normalized.value
    if isinstance(normalized, dict):
        return json.dumps(
            {str(key): serialize_json_compatible(item) for key, item in normalized.items()},
            sort_keys=True,
        )
    if isinstance(normalized, (list, tuple)):
        return json.dumps([serialize_json_compatible(item) for item in normalized])
    return str(normalized)


def serialize_json_compatible(value: Any) -> Any:
    normalized = normalize_value_for_project(value)
    if normalized is None:
        return None
    if isinstance(normalized, (str, int, float, bool)):
        return normalized
    if isinstance(normalized, dict):
        return {
            str(key): serialize_json_compatible(item) for key, item in normalized.items()
        }
    if isinstance(normalized, (list, tuple)):
        return [serialize_json_compatible(item) for item in normalized]
    return str(normalized)


def write_csv(path: Path, *, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field_name: serialize_csv_value(row.get(field_name))
                    for field_name in fieldnames
                }
            )


def assert_shadow_row_matches_context(
    log_row: ResolutionLog,
    *,
    current_value: Any,
    resolved_value: Any,
) -> None:
    normalized_current = normalize_value_for_project(current_value)
    normalized_resolved = normalize_value_for_project(resolved_value)
    if normalized_current != log_row.current_value:
        raise ValueError(
            "Current project value drifted from resolution_log for "
            f"project {log_row.project_id} field {log_row.field}: "
            f"{normalized_current!r} != {log_row.current_value!r}"
        )
    if normalized_resolved != log_row.resolved_value:
        raise ValueError(
            "Resolved value drifted from resolution_log for "
            f"project {log_row.project_id} field {log_row.field}: "
            f"{normalized_resolved!r} != {log_row.resolved_value!r}"
        )


def developer_review_cluster(current_value: Any) -> str:
    if str(current_value or "").strip() == "Helio / UCLA":
        return HELIO_CLUSTER_KEY
    return ""


def developer_review_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str, str]:
    rule_rank = DEVELOPER_REVIEW_RULE_ORDER.get(str(row.get("rule_applied") or ""), 99)
    cluster_rank = 0 if row.get("review_cluster") == HELIO_CLUSTER_KEY else 1
    return (
        rule_rank,
        cluster_rank,
        str(row.get("current_value") or ""),
        str(row.get("resolved_value") or ""),
        str(row.get("canonical_address") or ""),
    )


def is_likely_alias_candidate(row: dict[str, Any]) -> bool:
    pair = (str(row.get("current_value") or ""), str(row.get("resolved_value") or ""))
    return pair in LIKELY_ALIAS_CANDIDATES


def select_delivery_estimate_spotcheck(
    rows: list[dict[str, Any]],
    *,
    sample_size: int = DEFAULT_ESTIMATE_SAMPLE_SIZE,
    seed: int = DEFAULT_ESTIMATE_SAMPLE_SEED,
) -> list[dict[str, Any]]:
    if len(rows) <= sample_size:
        return sorted(rows, key=lambda row: str(row.get("canonical_address") or ""))

    stable_rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("project_id") or ""),
            str(row.get("canonical_address") or ""),
        ),
    )
    chooser = random.Random(seed)
    chosen_indexes = sorted(chooser.sample(range(len(stable_rows)), sample_size))
    return [stable_rows[index] for index in chosen_indexes]


def base_review_row(
    *,
    log_row: ResolutionLog,
    context: ProjectReviewContext,
    current_value: Any,
    resolved_value: Any,
    rule_applied: str,
    resolution_confidence: Any,
    evidence_ids: list[Any],
    frontier: dict[str, Any] | None,
    winning_evidence: Evidence | None,
) -> dict[str, Any]:
    return {
        "decision": "",
        "notes": "",
        "project_id": str(context.project.id),
        "project_name": context.project.project_name or "",
        "canonical_address": context.project.canonical_address,
        "field": log_row.field,
        "current_value": current_value,
        "resolved_value": resolved_value,
        "rule_applied": rule_applied,
        "resolution_confidence": (
            resolution_confidence.value
            if hasattr(resolution_confidence, "value")
            else resolution_confidence
        ),
        "project_confidence": (
            context.project.confidence.value
            if hasattr(context.project.confidence, "value")
            else context.project.confidence
        ),
        "delta_shape": classify_delta_shape(current_value, resolved_value),
        "last_evidence_date": context.last_evidence_date,
        "evidence_count": context.evidence_count,
        "supporting_evidence_count": len(evidence_ids),
        "evidence_ids": [str(evidence_id) for evidence_id in evidence_ids],
        "winning_source_type": frontier.get("source_type") if isinstance(frontier, dict) else None,
        "winning_source_tier": frontier.get("source_tier") if isinstance(frontier, dict) else None,
        "winning_evidence_date": (
            frontier.get("evidence_date") if isinstance(frontier, dict) else None
        ),
        "winning_collected_at": (
            frontier.get("collected_at") if isinstance(frontier, dict) else None
        ),
        "winning_source_record_id": winning_evidence.source_record_id if winning_evidence else None,
    }
