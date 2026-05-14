from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator


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


class ProjectGeocodeMutationResponse(BaseModel):
    project_id: uuid.UUID
    geocoding: ProjectGeocodingResponse
    latitude: float | None
    longitude: float | None
    geocode_confidence: str
    updated_coordinates: bool
    change_log_entries_created: int


class ProjectRelationshipCreateRequest(BaseModel):
    relationship_type: str = Field(min_length=1, max_length=50)
    related_project_id: uuid.UUID
    notes: str | None = Field(default=None, max_length=2000)


class ProjectRelationshipUpdateRequest(BaseModel):
    relationship_type: str | None = Field(default=None, min_length=1, max_length=50)
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


class ReviewEvidenceSummary(BaseModel):
    evidence_id: uuid.UUID
    stance: str
    is_winning: bool
    source_type: str
    source_tier: int
    source_record_id: str | None
    evidence_date: str | None
    collected_at: str
    summary: str
    detail: str
    # Source-type-specific structured fields surfaced on review cards. Empty
    # dict for source types that don't define structured fields. See
    # SnippetPayload.source_fields in review/snippets.py.
    source_fields: dict[str, Any] = {}
    external_link: str | None
    highlights: list[dict[str, Any]]
    extracted_value: Any | None


class ReviewValueChangePayload(BaseModel):
    field_name: str
    field_label: str
    field_type: str
    current_value: Any | None
    evidence_value: Any | None
    agent_recommended_value: Any | None
    default_result_value: Any | None
    constraints: dict[str, Any] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    dissenting_evidence_ids: list[str] = Field(default_factory=list)
    human_summary: str | None = None


class ReviewQueueItemResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    source_run_id: uuid.UUID | None
    item_type: str
    status: str
    state: str
    priority: str
    match_confidence: float | None
    field_name: str | None
    winning_evidence_id: uuid.UUID | None
    payload: Any | None
    assigned_to: str | None
    created_at: str
    resolved_at: str | None
    resolved_by: str | None
    active_decision: ReviewDecisionSummary | None
    value_change: ReviewValueChangePayload | None = None
    evidence_summaries: list[ReviewEvidenceSummary] = []


class ReviewDedupCandidatesResponse(BaseModel):
    subject: dict[str, Any]
    candidates: list[dict[str, Any]]
    layer_3_available: bool
    new_candidate_probability: float
    searched: dict[str, Any]


class ReviewMatchPreviewResponse(BaseModel):
    review_items_to_close: int
    evidence_rows_to_reattach: int
    value_change_items_that_would_be_queued: list[str]


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


class ActivityProjectSummary(BaseModel):
    id: uuid.UUID
    project_name: str | None
    canonical_address: str
    city: str | None
    state: str | None
    zip: str | None
    pipeline_status: str


class ActivityArticleSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    url: str
    source_slug: str | None
    source_name: str | None
    fetched_at: str | None
    published_at: str | None


class ActivityPermitSummary(BaseModel):
    source_record_id: str | None = None
    permit_number: str | None = None
    permit_type: str | None = None
    issue_date: str | None = None
    address: str | None = None
    apn: str | None = None
    status: str | None = None


class ActivityIntakeSummary(BaseModel):
    kind: str
    label: str | None = None
    article: ActivityArticleSummary | None = None
    permit: ActivityPermitSummary | None = None


class ActivityEvidenceSummary(BaseModel):
    evidence_id: uuid.UUID
    source_type: str
    source_tier: int
    source_record_id: str | None
    role: str | None = None
    evidence_date: str | None
    collected_at: str
    summary: str
    detail: str
    external_link: str | None
    highlights: list[dict[str, Any]]
    extracted_value: Any | None


class ActivityReviewItemSummary(BaseModel):
    id: uuid.UUID
    human_summary: str


class ActivityEventResponse(BaseModel):
    id: str
    event_type: str
    occurred_at: str
    project: ActivityProjectSummary | None
    source: str
    source_label: str
    field: str | None = None
    field_label: str | None = None
    actor_label: str | None = None
    title: str
    summary: str
    old_value: Any | None = None
    new_value: Any | None = None
    change_type: str | None = None
    priority: str | None = None
    review_item_id: uuid.UUID | None = None
    review_item_ids: list[uuid.UUID] = Field(default_factory=list)
    review_item_summaries: list[ActivityReviewItemSummary] = Field(default_factory=list)
    article: ActivityArticleSummary | None = None
    intake_summary: ActivityIntakeSummary | None = Field(
        default=None,
        description="Source-row context, populated for agent and semantic events.",
    )
    article_fetched_at: str | None = None
    agent_created_at: str | None = None
    agent_outcome: str | None = None
    agent_triggers: list[str] = Field(default_factory=list)
    agent_reasoning_trace: str | None = None
    cost_usd: float | None = None
    evidence_summaries: list[ActivityEvidenceSummary] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


class ActivityFeedResponse(BaseModel):
    generated_at: str
    events: list[ActivityEventResponse]
    next_cursor: str | None = None


class ActivitySemanticMetricResponse(BaseModel):
    market: str | None
    source_slug: str | None
    source_name: str | None
    field_name: str
    field_label: str
    reason_code: str
    total_count: int
    glossary_gap_count: int
    unmappable_count: int
    glossary_gap_rate: float
    unmappable_rate: float
    reviewer_decision_count: int = 0
    reviewer_rejection_count: int = 0
    reviewer_rejection_rate: float | None = None


class ActivitySemanticParseStatusResponse(BaseModel):
    parse_status: str
    total_count: int
    rate: float


class ActivitySemanticParseHealthResponse(BaseModel):
    total_count: int
    ok_count: int
    failure_count: int
    ok_rate: float
    failure_rate: float
    statuses: list[ActivitySemanticParseStatusResponse]


class ActivitySemanticMetricsResponse(BaseModel):
    generated_at: str
    thresholds: dict[str, float]
    parse_health: ActivitySemanticParseHealthResponse
    metrics: list[ActivitySemanticMetricResponse]


class CoverageScrapeRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=120)


class ScrapeJobResponse(BaseModel):
    id: uuid.UUID
    jurisdiction_id: uuid.UUID | None
    kind: str
    source_name: str
    target_payload: Any | None
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


class NewsSourceHealthResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    active: bool
    paused: bool
    fetch_path: str
    schedule_cron: str | None
    schedule_timezone: str | None
    source_strategy_doc: str | None
    last_run_at: str | None
    last_run_finished_at: str | None
    last_run_had_error: bool
    discovered_count: int | None
    fetched_count: int | None
    failed_count: int | None
    block_like_failure_count: int | None
    transient_failure_count: int | None
    cost_cap_skipped_count: int | None
    last_alert_key: str | None
    last_alert_severity: str | None
    last_alert_message: str | None
    last_alert_at: str | None


class CoStarUploadResponse(BaseModel):
    id: uuid.UUID
    jurisdiction_id: uuid.UUID
    file_name: str
    file_size_bytes: int | None
    row_count: int | None
    source_run_id: uuid.UUID | None
    status: str
    error_text: str | None


class ResearchArticleCreateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4000)
    force_reextract: bool = False
    force_project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional project UUID hint used by the news matcher for "
            "single-reference paste-a-link articles."
        ),
    )
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("url")
    @classmethod
    def url_must_be_absolute_http_url(cls, value: str) -> str:
        text = value.strip()
        if not text.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://.")
        return text


class ResearchArticleCreateResponse(BaseModel):
    article_id: uuid.UUID
    scrape_job_id: uuid.UUID | None
    status: str
    existing_article: bool


class ResearchArticleRetryResponse(BaseModel):
    article_id: uuid.UUID
    scrape_job_id: uuid.UUID
    status: str
    existing_active_job: bool


class ResearchArticleDetail(BaseModel):
    id: uuid.UUID
    news_source_id: uuid.UUID
    source_name: str
    url_canonical: str
    url_original: str
    fetch_status: str
    fetch_attempts: int
    fetched_at: str | None
    fetch_error_text: str | None
    http_status: int | None
    title: str | None
    byline_author: str | None
    published_at: str | None
    publication_section: str | None
    tags: list[str] | None
    external_article_id: str | None
    language: str
    paywall_state: str | None
    body_text: str | None
    body_text_hash: str | None
    raw_html_hash: str | None
    structural_signals_at: str | None
    triage_status: str | None
    triage_at: str | None
    triage_extraction_id: uuid.UUID | None
    current_extraction_id: uuid.UUID | None
    current_extraction_version: int
    ingest_method: str
    ingested_by_user_id: uuid.UUID | None
    notes: str | None
    created_at: str
    updated_at: str


class ResearchExtractionSummary(BaseModel):
    id: uuid.UUID
    pass_name: str
    triggered_by: str
    prompt_id: str
    prompt_version: str
    model: str
    parse_status: str
    created_at: str


class ResearchReferenceSummary(BaseModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    reference_index: int
    candidate_name: str | None
    candidate_address: str | None
    candidate_city: str | None
    candidate_developer: str | None
    match_status: str
    matched_project_id: uuid.UUID | None


class ResearchArticleDetailResponse(BaseModel):
    article: ResearchArticleDetail
    scrape_jobs: list[ScrapeJobResponse]
    extractions: list[ResearchExtractionSummary]
    references: list[ResearchReferenceSummary]
