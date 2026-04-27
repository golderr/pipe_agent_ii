from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.api.errors import raise_not_implemented
from tcg_pipeline.api.project_fields import append_project_note as append_project_note_value
from tcg_pipeline.api.project_fields import update_project_field as update_project_field_value
from tcg_pipeline.api.project_overrides import (
    clear_project_override as clear_project_override_value,
)
from tcg_pipeline.api.project_overrides import (
    set_project_override as set_project_override_value,
)
from tcg_pipeline.api.project_relationships import (
    add_project_relationship as add_project_relationship_value,
)
from tcg_pipeline.api.schemas import (
    ProjectFieldMutationResponse,
    ProjectFieldUpdateRequest,
    ProjectNoteAppendRequest,
    ProjectNoteAppendResponse,
    ProjectOverrideMutationResponse,
    ProjectOverrideSetRequest,
    ProjectRelationshipCreateRequest,
    ProjectRelationshipMutationResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)
JSON_BODY = Body(default_factory=dict)


@router.post("")
def create_project(
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented("project creation")


@router.get("/{project_id}")
def get_project(
    project_id: uuid.UUID,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"project detail API read for {project_id}")


@router.post("/{project_id}/field")
def update_project_field(
    project_id: uuid.UUID,
    payload: ProjectFieldUpdateRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectFieldMutationResponse:
    return update_project_field_value(
        session,
        project_id=project_id,
        field_name=payload.field_name,
        value=payload.value,
        user=user,
    )


@router.post("/{project_id}/override")
def set_project_override(
    project_id: uuid.UUID,
    payload: ProjectOverrideSetRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectOverrideMutationResponse:
    return set_project_override_value(
        session,
        project_id=project_id,
        field_name=payload.field_name,
        value=payload.value,
        note=payload.note,
        source_url=payload.source_url,
        user=user,
    )


@router.delete("/{project_id}/override/{field_name}")
def clear_project_override(
    project_id: uuid.UUID,
    field_name: str,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectOverrideMutationResponse:
    return clear_project_override_value(
        session,
        project_id=project_id,
        field_name=field_name,
        user=user,
    )


@router.post("/{project_id}/note")
def add_project_note(
    project_id: uuid.UUID,
    payload: ProjectNoteAppendRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectNoteAppendResponse:
    return append_project_note_value(
        session,
        project_id=project_id,
        note_type=payload.note_type,
        body=payload.body,
        user=user,
    )


@router.post("/{project_id}/relationship")
def add_project_relationship(
    project_id: uuid.UUID,
    payload: ProjectRelationshipCreateRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectRelationshipMutationResponse:
    return add_project_relationship_value(
        session,
        project_id=project_id,
        relationship_type=payload.relationship_type,
        related_project_id=payload.related_project_id,
        notes=payload.notes,
        user=user,
    )
