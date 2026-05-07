from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.schemas import ProjectOverrideMutationResponse
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    AgeRestriction,
    ChangeLog,
    ChangeType,
    PipelineStatus,
    Priority,
    ProductType,
    Project,
)
from tcg_pipeline.db.researcher_overrides import (
    active_researcher_overrides_for_project,
    clear_researcher_override_fields,
    upsert_researcher_overrides,
)
from tcg_pipeline.db.review_workflow import CHANGELOG_PRIORITY_BY_FIELD
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.resolution.engine import normalize_value_for_project
from tcg_pipeline.resolution.fields import FieldResolution, parse_date_value

EVIDENCE_DERIVED_OVERRIDE_FIELDS = frozenset(
    {
        "pipeline_status",
        "total_units",
        "affordable_units",
        "market_rate_units",
        "workforce_units",
        "developer",
        "product_type",
        "age_restriction",
        "date_delivery",
    }
)

INTEGER_FIELDS = frozenset(
    {"total_units", "affordable_units", "market_rate_units", "workforce_units"}
)


def set_project_override(
    session: Session,
    *,
    project_id: uuid.UUID,
    field_name: str,
    value: Any,
    note: str | None,
    source_url: str | None,
    user: AuthenticatedUser,
) -> ProjectOverrideMutationResponse:
    field_name = _validate_override_field(field_name)
    project = _load_project(session, project_id)
    override_value = _coerce_override_value(field_name, value)
    now = datetime.now(UTC)
    actor = _actor_for_audit(user)
    old_value = _project_field_value(project, field_name)
    # Capture the resolver frontier before the manual value is written. For
    # review-protected overrides this baseline is audit data and the point of
    # comparison for future contradiction review items.
    pre_override_resolution = resolve_project(
        project.id,
        session,
        apply=False,
        write_resolution_log=False,
    )
    upsert_researcher_overrides(
        session,
        project,
        {
            field_name: {
                "value": override_value,
                "set_by": actor,
                "set_at": now.isoformat(),
                "note": _clean_text(note),
                "source_url": _clean_text(source_url),
                "mode": "review_protected",
                "baseline": _baseline_for_resolution(
                    pre_override_resolution.field_resolutions.get(field_name)
                ),
            }
        },
        set_by_user_id=user.user_id,
    )
    _mark_project_edited(project, actor=actor, timestamp=now)
    session.flush()
    resolution_result = resolve_project(
        project.id,
        session,
        apply=True,
        write_resolution_log=True,
    )
    change_log_entries_created = _write_override_change_log(
        session,
        project=project,
        field_name=field_name,
        old_value=old_value,
        new_value=resolution_result.resolved_values.get(field_name),
        actor=actor,
        user=user,
        timestamp=now,
    )
    session.flush()
    return ProjectOverrideMutationResponse(
        project_id=project.id,
        field_name=field_name,
        old_value=serialize_json(old_value),
        new_value=serialize_json(override_value),
        resolved_value=serialize_json(resolution_result.resolved_values.get(field_name)),
        changed_fields=sorted(resolution_result.changed_fields),
        change_log_entries_created=change_log_entries_created,
    )


def clear_project_override(
    session: Session,
    *,
    project_id: uuid.UUID,
    field_name: str,
    user: AuthenticatedUser,
) -> ProjectOverrideMutationResponse:
    field_name = _validate_override_field(field_name)
    project = _load_project(session, project_id)
    active_overrides = active_researcher_overrides_for_project(session, project)
    if field_name not in active_overrides:
        raise HTTPException(status_code=404, detail="Active override not found.")

    now = datetime.now(UTC)
    actor = _actor_for_audit(user)
    old_value = _project_field_value(project, field_name)
    clear_researcher_override_fields(
        session,
        project,
        {field_name},
        cleared_by_user_id=user.user_id,
        cleared_at=now,
    )
    _mark_project_edited(project, actor=actor, timestamp=now)
    session.flush()
    resolution_result = resolve_project(
        project.id,
        session,
        apply=True,
        write_resolution_log=True,
    )
    resolved_value = resolution_result.resolved_values.get(field_name)
    change_log_entries_created = _write_override_change_log(
        session,
        project=project,
        field_name=field_name,
        old_value=old_value,
        new_value=resolved_value,
        actor=actor,
        user=user,
        timestamp=now,
    )
    session.flush()
    return ProjectOverrideMutationResponse(
        project_id=project.id,
        field_name=field_name,
        old_value=serialize_json(old_value),
        new_value=serialize_json(resolved_value),
        resolved_value=serialize_json(resolved_value),
        changed_fields=sorted(resolution_result.changed_fields),
        change_log_entries_created=change_log_entries_created,
        cleared=True,
    )


def _validate_override_field(field_name: str) -> str:
    normalized = field_name.strip()
    if normalized not in EVIDENCE_DERIVED_OVERRIDE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"{normalized or 'field'} is not an editable evidence-derived field.",
        )
    return normalized


def _coerce_override_value(field_name: str, value: Any) -> Any:
    if field_name in INTEGER_FIELDS:
        return _coerce_nonnegative_int(field_name, value)
    if field_name == "pipeline_status":
        return _coerce_enum(field_name, value, PipelineStatus)
    if field_name == "product_type":
        return _coerce_enum(field_name, value, ProductType)
    if field_name == "age_restriction":
        return _coerce_enum(field_name, value, AgeRestriction)
    if field_name == "date_delivery":
        parsed = parse_date_value(value)
        if parsed is None:
            raise HTTPException(status_code=422, detail="date_delivery must be a YYYY-MM-DD date.")
        return parsed
    if field_name == "developer":
        text_value = _clean_text(value)
        if text_value is None:
            raise HTTPException(status_code=422, detail="developer must be a non-empty string.")
        return text_value
    raise HTTPException(status_code=400, detail=f"{field_name} cannot be edited as an override.")


def _coerce_nonnegative_int(field_name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{field_name} must be a non-negative integer.")
    if isinstance(value, float) and not value.is_integer():
        raise HTTPException(status_code=422, detail=f"{field_name} must be a non-negative integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a non-negative integer.",
        ) from exc
    if parsed < 0:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a non-negative integer.")
    return parsed


def _coerce_enum(field_name: str, value: Any, enum_cls) -> Any:
    try:
        return enum_cls(str(value).strip())
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be one of: {allowed}.",
        ) from exc


def _load_project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _actor_for_audit(user: AuthenticatedUser) -> str:
    return user.email or str(user.user_id)


def _mark_project_edited(project: Project, *, actor: str, timestamp: datetime) -> None:
    project.last_editor = actor[:50]
    project.last_edit_date = timestamp.date()


def _project_field_value(project: Project, field_name: str) -> Any:
    return normalize_value_for_project(getattr(project, field_name))


def _write_override_change_log(
    session: Session,
    *,
    project: Project,
    field_name: str,
    old_value: Any,
    new_value: Any,
    actor: str,
    user: AuthenticatedUser,
    timestamp: datetime,
) -> int:
    priority = CHANGELOG_PRIORITY_BY_FIELD.get(field_name, Priority.MEDIUM)
    session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=timestamp,
            source="inline_override",
            field=field_name,
            old_value=serialize_json(old_value),
            new_value=serialize_json(normalize_value_for_project(new_value)),
            change_type=ChangeType.RESEARCHER_OVERRIDE,
            priority=priority,
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )
    return 1


def _baseline_for_resolution(resolution: FieldResolution | None) -> dict[str, Any] | None:
    if resolution is None:
        return None
    frontier = resolution.metadata.get("evidence_frontier")
    if not isinstance(frontier, Mapping):
        return None
    return {
        "evidence_date": serialize_json(frontier.get("evidence_date")),
        "collected_at": serialize_json(frontier.get("collected_at")),
        "source_tier": frontier.get("source_tier"),
        "source_type": frontier.get("source_type"),
        "evidence_ids": [str(evidence_id) for evidence_id in resolution.evidence_ids],
        "rule_applied": resolution.rule_applied,
    }


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
