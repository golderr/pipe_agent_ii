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


class ProjectCreateRequest(BaseModel):
    canonical_address: str = Field(min_length=1, max_length=255)
    market_id: uuid.UUID
    jurisdiction_id: uuid.UUID
    project_name: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    county: str | None = Field(default=None, max_length=120)
    zip: str | None = Field(default=None, max_length=10)
    force_create: bool = False


class ProjectCreateCandidate(BaseModel):
    project_id: uuid.UUID
    project_name: str
    canonical_address: str
    pipeline_status: str
    match_type: str
    confidence: float | None


class ProjectGeocodingResponse(BaseModel):
    status: str
    provider: str | None = None
    confidence: str
    formatted_address: str | None = None
    accuracy_type: str | None = None
    accuracy_score: float | None = None
    fallback_used: bool = False
    message: str | None = None


class ProjectCreateResponse(BaseModel):
    created: bool
    project_id: uuid.UUID | None
    canonical_address: str
    duplicate_candidates: list[ProjectCreateCandidate]
    change_log_entries_created: int
    geocoding: ProjectGeocodingResponse | None = None


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


class ReviewDecisionStageRequest(BaseModel):
    decision_type: str = Field(min_length=1, max_length=50)
    decision_value: Any | None = None
    notes: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=2000)


class ReviewDecisionStageResponse(BaseModel):
    review_item_id: uuid.UUID
    decision_id: uuid.UUID
    decision_type: str
    item_state: str
    staged_by: uuid.UUID | None
    staged_by_email: str | None
    revised: bool


class ReviewDecisionSummary(BaseModel):
    decision_id: uuid.UUID
    state: str
    decision_type: str | None
    staged_at: str | None
    staged_by: uuid.UUID | None
    staged_by_email: str | None
    committed_at: str | None
    committed_by: uuid.UUID | None
    committed_by_email: str | None
    decision_value: Any | None
    decision_notes: str | None
    source_url: str | None


class ReviewQueueItemResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    source_run_id: uuid.UUID | None
    item_type: str
    status: str
    state: str
    priority: str
    match_confidence: float | None
    payload: Any | None
    assigned_to: str | None
    created_at: str
    resolved_at: str | None
    resolved_by: str | None
    active_decision: ReviewDecisionSummary | None


class ReviewCommitRequest(BaseModel):
    jurisdiction_id: uuid.UUID | None = None
    dry_run: bool = False


class ReviewCommitResponse(BaseModel):
    committed_decisions: int
    affected_projects: int
    field_changes_applied: int
    review_items_committed: int
    review_items_remaining: int
    deferred_items: int
    jurisdictions_touched: list[uuid.UUID]
    queue_cleared: bool
    dry_run: bool


class CoverageScrapeRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=120)


class ScrapeJobResponse(BaseModel):
    id: uuid.UUID
    jurisdiction_id: uuid.UUID
    source_name: str
    trigger_type: str
    initiated_by_user_id: uuid.UUID | None
    initiated_by_email: str | None
    status: str
    queued_at: str
    started_at: str | None
    completed_at: str | None
    source_run_id: uuid.UUID | None
    error_text: str | None
    progress: Any | None


class ScrapeWorkerHealthResponse(BaseModel):
    configured: bool
    available: bool
    queue_name: str
    queued_jobs: int
    started_jobs: int
    failed_jobs: int
    worker_count: int
    error: str | None = None


class CoStarUploadResponse(BaseModel):
    id: uuid.UUID
    jurisdiction_id: uuid.UUID
    file_name: str
    file_size_bytes: int | None
    row_count: int | None
    source_run_id: uuid.UUID | None
    status: str
    error_text: str | None
