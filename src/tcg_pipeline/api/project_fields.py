from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.schemas import (
    ProjectFieldMutationResponse,
    ProjectNoteAppendResponse,
)
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    Priority,
    Project,
    ProjectNote,
)
from tcg_pipeline.resolution.engine import normalize_value_for_project

PROJECT_NOTE_FIELDS = frozenset({"researcher_notes", "personal_notes", "change_notes"})

FIELD_COERCERS: dict[str, Callable[[Any], Any]] = {
    "project_name": lambda value: _coerce_optional_text("project_name", value, max_length=255),
    "previous_names": lambda value: _coerce_string_list("previous_names", value),
    "raw_addresses": lambda value: _coerce_string_list(
        "raw_addresses",
        value,
        allow_empty=False,
    ),
    "city": lambda value: _coerce_required_text("city", value, max_length=120),
    "state": lambda value: _coerce_state(value),
    "county": lambda value: _coerce_required_text("county", value, max_length=120),
    "zip": lambda value: _coerce_optional_text("zip", value, max_length=10),
    "tcg_region": lambda value: _coerce_optional_text("tcg_region", value, max_length=150),
    "source_urls": lambda value: _coerce_string_list(
        "source_urls",
        value,
        validate_url=True,
    ),
    "planner_1_name": lambda value: _coerce_optional_text("planner_1_name", value, max_length=255),
    "planner_1_city": lambda value: _coerce_optional_text("planner_1_city", value, max_length=120),
    "planner_1_email": lambda value: _coerce_optional_text(
        "planner_1_email",
        value,
        max_length=255,
    ),
    "planner_1_phone": lambda value: _coerce_optional_text("planner_1_phone", value, max_length=50),
    "planner_2_name": lambda value: _coerce_optional_text("planner_2_name", value, max_length=255),
    "planner_2_city": lambda value: _coerce_optional_text("planner_2_city", value, max_length=120),
    "planner_2_email": lambda value: _coerce_optional_text(
        "planner_2_email",
        value,
        max_length=255,
    ),
    "planner_2_phone": lambda value: _coerce_optional_text("planner_2_phone", value, max_length=50),
    "inclusion_in_analysis": lambda value: _coerce_bool(value),
    "inclusion_in_exhibit": lambda value: _coerce_bool(value),
    "inclusion_note": lambda value: _coerce_optional_text("inclusion_note", value, max_length=255),
}


def update_project_field(
    session: Session,
    *,
    project_id: uuid.UUID,
    field_name: str,
    value: Any,
    user: AuthenticatedUser,
) -> ProjectFieldMutationResponse:
    field_name = _validate_project_field(field_name)
    project = _load_project(session, project_id)
    old_value = _project_field_value(project, field_name)
    new_value = FIELD_COERCERS[field_name](value)
    actor = _actor_for_audit(user)
    now = datetime.now(UTC)

    setattr(project, field_name, new_value)
    _mark_project_edited(project, actor=actor, timestamp=now)
    change_log_entries_created = 0
    if old_value != normalize_value_for_project(new_value):
        change_log_entries_created = _write_direct_change_log(
            session,
            project=project,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            actor=actor,
            user=user,
            timestamp=now,
            source="inline_field",
        )
    session.flush()
    return ProjectFieldMutationResponse(
        project_id=project.id,
        field_name=field_name,
        old_value=serialize_json(old_value),
        new_value=serialize_json(normalize_value_for_project(new_value)),
        change_log_entries_created=change_log_entries_created,
    )


def append_project_note(
    session: Session,
    *,
    project_id: uuid.UUID,
    note_type: str,
    body: str,
    user: AuthenticatedUser,
) -> ProjectNoteAppendResponse:
    note_type = _validate_note_type(note_type)
    project = _load_project(session, project_id)
    note_body = _coerce_note_body(body)
    old_value = _project_field_value(project, note_type)
    actor = _actor_for_audit(user)
    now = datetime.now(UTC)
    note = ProjectNote(
        project_id=project.id,
        note_type=note_type,
        body=note_body,
        created_by_user_id=user.user_id,
        created_by_label=actor,
        created_at=now,
    )
    session.add(note)
    setattr(project, note_type, note_body)
    _mark_project_edited(project, actor=actor, timestamp=now)
    change_log_entries_created = _write_direct_change_log(
        session,
        project=project,
        field_name=note_type,
        old_value=old_value,
        new_value=note_body,
        actor=actor,
        user=user,
        timestamp=now,
        source="project_note",
    )
    session.flush()
    return ProjectNoteAppendResponse(
        project_id=project.id,
        note_id=note.id,
        note_type=note.note_type,
        body=note.body,
        created_at=note.created_at.isoformat(),
        change_log_entries_created=change_log_entries_created,
    )


def _validate_project_field(field_name: str) -> str:
    normalized = field_name.strip()
    if normalized in PROJECT_NOTE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"{normalized} must be appended through the project note endpoint.",
        )
    if normalized not in FIELD_COERCERS:
        raise HTTPException(
            status_code=400,
            detail=f"{normalized or 'field'} is not an editable researcher-authored field.",
        )
    return normalized


def _validate_note_type(note_type: str) -> str:
    normalized = note_type.strip()
    if normalized not in PROJECT_NOTE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"{normalized or 'note'} is not an editable project note type.",
        )
    return normalized


def _load_project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _coerce_required_text(field_name: str, value: Any, *, max_length: int) -> str:
    text = _clean_text(value)
    if text is None:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a non-empty string.")
    if len(text) > max_length:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be {max_length} characters or fewer.",
        )
    return text


def _coerce_optional_text(field_name: str, value: Any, *, max_length: int) -> str | None:
    text = _clean_text(value)
    if text is not None and len(text) > max_length:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be {max_length} characters or fewer.",
        )
    return text


def _coerce_state(value: Any) -> str:
    text = _clean_text(value)
    if text is None:
        raise HTTPException(status_code=422, detail="state must be a non-empty string.")
    text = text.upper()
    if len(text) != 2:
        raise HTTPException(
            status_code=422,
            detail="state must be a 2-character postal abbreviation.",
        )
    return text


def _coerce_string_list(
    field_name: str,
    value: Any,
    *,
    allow_empty: bool = True,
    validate_url: bool = False,
) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = value.splitlines()
    else:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a list of strings.")
    items = [_clean_text(item) for item in raw_items]
    normalized = [item for item in items if item is not None]
    if not allow_empty and not normalized:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must include at least one value.",
        )
    if validate_url:
        for item in normalized:
            _validate_url(field_name, item)
    return normalized


def _validate_url(field_name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} entries must be valid HTTP(S) URLs.",
        )


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    raise HTTPException(status_code=422, detail="value must be a boolean.")


def _coerce_note_body(value: Any) -> str:
    text = _clean_text(value)
    if text is None:
        raise HTTPException(status_code=422, detail="note body must be a non-empty string.")
    if len(text) > 10000:
        raise HTTPException(status_code=422, detail="note body must be 10000 characters or fewer.")
    return text


def _actor_for_audit(user: AuthenticatedUser) -> str:
    return user.email or str(user.user_id)


def _mark_project_edited(project: Project, *, actor: str, timestamp: datetime) -> None:
    project.last_editor = actor[:50]
    project.last_edit_date = timestamp.date()


def _project_field_value(project: Project, field_name: str) -> Any:
    return normalize_value_for_project(getattr(project, field_name))


def _write_direct_change_log(
    session: Session,
    *,
    project: Project,
    field_name: str,
    old_value: Any,
    new_value: Any,
    actor: str,
    user: AuthenticatedUser,
    timestamp: datetime,
    source: str,
) -> int:
    session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=timestamp,
            source=source,
            field=field_name,
            old_value=serialize_json(old_value),
            new_value=serialize_json(normalize_value_for_project(new_value)),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.LOW,
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )
    return 1


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
