from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_app_settings, get_db_session, require_user
from tcg_pipeline.api.errors import raise_not_implemented
from tcg_pipeline.api.project_creation import create_project as create_project_value
from tcg_pipeline.api.project_fields import append_project_note as append_project_note_value
from tcg_pipeline.api.project_fields import update_project_field as update_project_field_value
from tcg_pipeline.api.project_geocoding import geocode_project as geocode_project_value
from tcg_pipeline.api.project_overrides import (
    clear_project_override as clear_project_override_value,
)
from tcg_pipeline.api.project_overrides import (
    set_project_override as set_project_override_value,
)
from tcg_pipeline.api.project_relationships import (
    add_project_relationship as add_project_relationship_value,
)
from tcg_pipeline.api.project_relationships import (
    delete_project_relationship as delete_project_relationship_value,
)
from tcg_pipeline.api.project_relationships import (
    update_project_relationship as update_project_relationship_value,
)
from tcg_pipeline.api.schemas import (
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectFieldMutationResponse,
    ProjectFieldUpdateRequest,
    ProjectGeocodeMutationResponse,
    ProjectNoteAppendRequest,
    ProjectNoteAppendResponse,
    ProjectOverrideMutationResponse,
    ProjectOverrideSetRequest,
    ProjectRelationshipCreateRequest,
    ProjectRelationshipMutationResponse,
    ProjectRelationshipUpdateRequest,
)
from tcg_pipeline.geocoding.service import geocoder_from_settings
from tcg_pipeline.settings import Settings

router = APIRouter(prefix="/projects", tags=["projects"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)
APP_SETTINGS = Depends(get_app_settings)


@router.post("")
def create_project(
    payload: ProjectCreateRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    settings: Settings = APP_SETTINGS,
) -> ProjectCreateResponse:
    return create_project_value(
        session,
        canonical_address=payload.canonical_address,
        market_id=payload.market_id,
        jurisdiction_id=payload.jurisdiction_id,
        project_name=payload.project_name,
        city=payload.city,
        county=payload.county,
        zip_code=payload.zip,
        force_create=payload.force_create,
        user=user,
        geocoder=geocoder_from_settings(settings),
    )


@router.post("/{project_id}/geocode")
def geocode_project(
    project_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
    settings: Settings = APP_SETTINGS,
) -> ProjectGeocodeMutationResponse:
    return geocode_project_value(
        session,
        project_id=project_id,
        user=user,
        geocoder=geocoder_from_settings(settings),
    )


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


@router.patch("/{project_id}/relationship/{relationship_id}")
def update_project_relationship(
    project_id: uuid.UUID,
    relationship_id: uuid.UUID,
    payload: ProjectRelationshipUpdateRequest,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectRelationshipMutationResponse:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return update_project_relationship_value(
        session,
        project_id=project_id,
        relationship_id=relationship_id,
        relationship_type=payload.relationship_type,
        notes=payload.notes,
        notes_provided="notes" in fields_set,
        user=user,
    )


@router.delete("/{project_id}/relationship/{relationship_id}")
def delete_project_relationship(
    project_id: uuid.UUID,
    relationship_id: uuid.UUID,
    user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> ProjectRelationshipMutationResponse:
    return delete_project_relationship_value(
        session,
        project_id=project_id,
        relationship_id=relationship_id,
        user=user,
    )
