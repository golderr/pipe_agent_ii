from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import require_user
from tcg_pipeline.api.errors import raise_not_implemented

router = APIRouter(prefix="/projects", tags=["projects"])
AUTH_USER = Depends(require_user)
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
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"researcher-authored field update for {project_id}")


@router.post("/{project_id}/override")
def set_project_override(
    project_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"researcher override set for {project_id}")


@router.delete("/{project_id}/override/{field_name}")
def clear_project_override(
    project_id: uuid.UUID,
    field_name: str,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"researcher override clear for {project_id}.{field_name}")


@router.post("/{project_id}/note")
def add_project_note(
    project_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"project note append for {project_id}")


@router.post("/{project_id}/relationship")
def add_project_relationship(
    project_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"project relationship link for {project_id}")
