from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.models import PipelineStatus, Priority, Project
from tcg_pipeline.status_rules import StatusSuggestion, build_status_suggestion


@dataclass(slots=True)
class DetectedChange:
    field: str
    old_value: Any
    new_value: Any
    priority: Priority


@dataclass(slots=True)
class DiffResult:
    field_changes: list[DetectedChange] = field(default_factory=list)
    status_suggestion: StatusSuggestion | None = None

    @property
    def has_reviewable_changes(self) -> bool:
        return bool(self.field_changes or self.status_suggestion is not None)


@dataclass(frozen=True, slots=True)
class ProjectDiffSnapshot:
    pipeline_status: PipelineStatus
    status_date: date | None
    date_construction_start: date | None
    total_units: int | None


def diff_project_against_record(project: Project, raw_record: RawRecord) -> DiffResult:
    diff_result = DiffResult()
    mapped_fields = raw_record.mapped_fields

    status_suggestion = _build_status_suggestion(project=project, mapped_fields=mapped_fields)
    if status_suggestion is not None:
        diff_result.status_suggestion = status_suggestion

    new_pipeline_status = _coerce_pipeline_status(mapped_fields.get("pipeline_status"))
    if new_pipeline_status is not None and new_pipeline_status != project.pipeline_status:
        diff_result.field_changes.append(
            DetectedChange(
                field="pipeline_status",
                old_value=project.pipeline_status.value,
                new_value=new_pipeline_status.value,
                priority=_priority_for_status_change(new_pipeline_status),
            )
        )

    new_status_date = _parse_date(mapped_fields.get("status_date"))
    if new_status_date is not None and new_status_date != project.status_date:
        diff_result.field_changes.append(
            DetectedChange(
                field="status_date",
                old_value=project.status_date,
                new_value=new_status_date,
                priority=Priority.MEDIUM,
            )
        )

    new_construction_start = _parse_date(mapped_fields.get("date_construction_start"))
    if (
        new_construction_start is not None
        and new_construction_start != project.date_construction_start
    ):
        diff_result.field_changes.append(
            DetectedChange(
                field="date_construction_start",
                old_value=project.date_construction_start,
                new_value=new_construction_start,
                priority=Priority.HIGH,
            )
        )

    new_total_units = _parse_int(mapped_fields.get("total_units"))
    if new_total_units is not None and new_total_units != project.total_units:
        diff_result.field_changes.append(
            DetectedChange(
                field="total_units",
                old_value=project.total_units,
                new_value=new_total_units,
                priority=Priority.MEDIUM,
            )
        )

    return diff_result


def snapshot_project_for_diff(project: Project) -> ProjectDiffSnapshot:
    return ProjectDiffSnapshot(
        pipeline_status=project.pipeline_status,
        status_date=project.status_date,
        date_construction_start=project.date_construction_start,
        total_units=project.total_units,
    )


def diff_project_snapshots(
    previous: ProjectDiffSnapshot,
    current: ProjectDiffSnapshot,
    *,
    status_evidence_type: str | None = None,
    status_evidence_date: date | None = None,
    status_reason: str | None = None,
) -> DiffResult:
    diff_result = DiffResult()
    status_suggestion = build_status_suggestion(
        current_status=previous.pipeline_status,
        evidence_type=status_evidence_type,
        evidence_date=status_evidence_date,
        reason_override=status_reason,
    )
    if status_suggestion is not None:
        diff_result.status_suggestion = status_suggestion

    if previous.pipeline_status != current.pipeline_status and status_suggestion is None:
        diff_result.field_changes.append(
            DetectedChange(
                field="pipeline_status",
                old_value=previous.pipeline_status.value,
                new_value=current.pipeline_status.value,
                priority=_priority_for_status_change(current.pipeline_status),
            )
        )

    if current.status_date is not None and current.status_date != previous.status_date:
        diff_result.field_changes.append(
            DetectedChange(
                field="status_date",
                old_value=previous.status_date,
                new_value=current.status_date,
                priority=Priority.MEDIUM,
            )
        )

    if (
        current.date_construction_start is not None
        and current.date_construction_start != previous.date_construction_start
    ):
        diff_result.field_changes.append(
            DetectedChange(
                field="date_construction_start",
                old_value=previous.date_construction_start,
                new_value=current.date_construction_start,
                priority=Priority.HIGH,
            )
        )

    if current.total_units is not None and current.total_units != previous.total_units:
        diff_result.field_changes.append(
            DetectedChange(
                field="total_units",
                old_value=previous.total_units,
                new_value=current.total_units,
                priority=Priority.MEDIUM,
            )
        )

    return diff_result


def _build_status_suggestion(
    *,
    project: Project,
    mapped_fields: dict[str, Any],
) -> StatusSuggestion | None:
    evidence_type = _coerce_text(mapped_fields.get("status_evidence_type"))
    evidence_date = _parse_date(mapped_fields.get("status_evidence_date"))
    reason = _coerce_text(mapped_fields.get("status_evidence_reason"))

    return build_status_suggestion(
        current_status=project.pipeline_status,
        evidence_type=evidence_type,
        evidence_date=evidence_date,
        reason_override=reason,
    )


def _coerce_pipeline_status(value: Any) -> PipelineStatus | None:
    if not value:
        return None
    try:
        return PipelineStatus(str(value))
    except ValueError:
        return None


def _priority_for_status_change(status: PipelineStatus) -> Priority:
    if status in {PipelineStatus.UNDER_CONSTRUCTION, PipelineStatus.COMPLETE}:
        return Priority.HIGH
    return Priority.MEDIUM
def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
