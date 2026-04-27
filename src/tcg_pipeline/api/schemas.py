from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    app_env: str


class ReadyResponse(BaseModel):
    status: str
    database: str


class WhoAmIResponse(BaseModel):
    user_id: uuid.UUID
    email: str | None
    role: str
    actor_label: str


class ProjectOverrideSetRequest(BaseModel):
    field_name: str = Field(min_length=1, max_length=120)
    value: Any
    note: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=2000)


class ProjectOverrideMutationResponse(BaseModel):
    project_id: uuid.UUID
    field_name: str
    old_value: Any
    new_value: Any
    resolved_value: Any
    changed_fields: list[str]
    change_log_entries_created: int
    cleared: bool = False


class ProjectFieldUpdateRequest(BaseModel):
    field_name: str = Field(min_length=1, max_length=120)
    value: Any


class ProjectFieldMutationResponse(BaseModel):
    project_id: uuid.UUID
    field_name: str
    old_value: Any
    new_value: Any
    change_log_entries_created: int


class ProjectNoteAppendRequest(BaseModel):
    note_type: str = Field(min_length=1, max_length=50)
    body: str = Field(min_length=1, max_length=10000)


class ProjectNoteAppendResponse(BaseModel):
    project_id: uuid.UUID
    note_id: uuid.UUID
    note_type: str
    body: str
    created_at: str
    change_log_entries_created: int


class ProjectRelationshipCreateRequest(BaseModel):
    relationship_type: str = Field(min_length=1, max_length=50)
    related_project_id: uuid.UUID
    notes: str | None = Field(default=None, max_length=2000)


class ProjectRelationshipMutationResponse(BaseModel):
    project_id: uuid.UUID
    relationship_id: uuid.UUID
    relationship_type: str
    related_project_id: uuid.UUID
    notes: str | None
    created: bool
    updated: bool
    change_log_entries_created: int
