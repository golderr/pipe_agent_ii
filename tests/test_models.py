from sqlalchemy.orm import configure_mappers

from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    DeveloperAlias,
    Evidence,
    NewsArticle,
    NewsExtraction,
    NewsExtractionPass,
    NewsProjectReference,
    NewsSemanticInterpretation,
    NewsSignalFlag,
    NewsSource,
    Project,
    ProjectIdentifier,
    ProjectRelationship,
    RelationshipType,
    ResolutionLog,
    ReviewItemType,
    ScrapeJob,
    ScrapeJobKind,
    SystemAlert,
)


def _build_project(canonical_address: str) -> Project:
    return Project(
        canonical_address=canonical_address,
        raw_addresses=[canonical_address],
        market="los_angeles",
        city="LOS ANGELES",
        state="CA",
        county="LOS ANGELES",
    )


def test_project_relationships_are_bidirectional() -> None:
    configure_mappers()

    primary = _build_project("123 MAIN STREET")
    related = _build_project("125 MAIN STREET")
    relationship = ProjectRelationship(
        project=primary,
        related_project=related,
        relationship_type=RelationshipType.PHASE,
    )

    assert relationship in primary.outgoing_relationships
    assert relationship in related.incoming_relationships


def test_identifier_value_lookup_index_is_declared() -> None:
    index_names = {index.name for index in ProjectIdentifier.__table__.indexes}

    assert "ix_project_identifiers_value" in index_names


def test_evidence_indexes_are_declared() -> None:
    index_names = {index.name for index in Evidence.__table__.indexes}

    assert "ix_evidence_project_id" in index_names
    assert "ix_evidence_source_type" in index_names
    assert "ix_evidence_evidence_date" in index_names
    assert "ix_evidence_collected_at" in index_names
    assert "ix_evidence_active_project_resolution" in index_names
    assert "uq_evidence_source_type_source_record_id_raw_data_hash" in index_names
    assert "superseded_at" in Evidence.__table__.columns


def test_news_research_indexes_are_declared() -> None:
    news_source_indexes = {index.name for index in NewsSource.__table__.indexes}
    news_article_indexes = {index.name for index in NewsArticle.__table__.indexes}
    news_extraction_indexes = {index.name for index in NewsExtraction.__table__.indexes}
    semantic_interpretation_indexes = {
        index.name for index in NewsSemanticInterpretation.__table__.indexes
    }
    reference_indexes = {index.name for index in NewsProjectReference.__table__.indexes}
    flag_indexes = {index.name for index in NewsSignalFlag.__table__.indexes}

    assert "ix_news_sources_active" in news_source_indexes
    assert "schedule_timezone" in NewsSource.__table__.columns
    assert "ix_news_articles_published_at" in news_article_indexes
    assert "ix_news_articles_triage_status" in news_article_indexes
    assert "ix_news_extractions_article_id_created_at" in news_extraction_indexes
    assert (
        "ix_news_semantic_interpretations_article_id_created_at"
        in semantic_interpretation_indexes
    )
    assert (
        "ix_news_semantic_interpretations_prompt_id_version"
        in semantic_interpretation_indexes
    )
    assert "output_json" in NewsSemanticInterpretation.__table__.columns
    assert "prompt_hash" in NewsSemanticInterpretation.__table__.columns
    assert "ix_news_project_references_match_status" in reference_indexes
    assert "ix_news_signal_flag_registry_active" in flag_indexes


def test_scrape_job_news_extension_columns_are_declared() -> None:
    index_names = {index.name for index in ScrapeJob.__table__.indexes}

    assert "kind" in ScrapeJob.__table__.columns
    assert "target_payload" in ScrapeJob.__table__.columns
    assert ScrapeJob.__table__.columns["jurisdiction_id"].nullable is True
    assert "ix_scrape_jobs_kind_status" in index_names
    assert "ix_scrape_jobs_article_id_kind_status_queued_at" in index_names
    assert "uq_scrape_jobs_one_active_collector" in index_names
    assert "uq_scrape_jobs_one_active_news_scrape" in index_names
    assert {member.value for member in ScrapeJobKind} == {
        "collector_run",
        "news_scrape",
        "news_paste_a_link",
        "news_reextract",
        "news_backfill_chunk",
    }
    assert NewsExtractionPass.REEXTRACTION.value == "reextraction"
    assert NewsExtractionPass.EXTRACT_RETRY.value == "extract_retry"


def test_agent_run_audit_tables_are_declared() -> None:
    configure_mappers()

    agent_run_indexes = {index.name for index in AgentRun.__table__.indexes}
    link_indexes = {index.name for index in AgentRunReviewItem.__table__.indexes}
    constraint_names = {constraint.name for constraint in AgentRun.__table__.constraints}

    assert "ix_agent_runs_intake" in agent_run_indexes
    assert "ix_agent_runs_project" in agent_run_indexes
    assert "ix_agent_runs_profile_outcome" in agent_run_indexes
    assert "ix_agent_runs_source_run" in agent_run_indexes
    assert "ix_agent_runs_created_at" in agent_run_indexes
    assert "ix_agent_run_review_items_review_item" in link_indexes
    assert "ck_agent_runs_triggered_by_nonempty_array" in constraint_names
    assert "ck_agent_runs_evidence_consulted_array" in constraint_names
    assert "ck_agent_runs_tool_calls_summary_array" in constraint_names
    assert "ck_agent_runs_outcome" in constraint_names
    assert "ck_agent_runs_nonnegative_counters" in constraint_names
    assert "ck_agent_runs_failed_outcome_error_text" in constraint_names
    assert AgentRun.__table__.columns["evidence_consulted"].nullable is False
    assert AgentRun.__table__.columns["tool_calls_summary"].nullable is False
    assert AgentRun.__table__.columns["completed_at"].nullable is False
    assert AgentRun.__table__.columns["evidence_consulted"].server_default is not None
    assert AgentRun.__table__.columns["tool_calls_summary"].server_default is not None
    assert "news_articles.id" in AgentRun.__table__.columns["intake_record_id"].comment
    assert {member.value for member in AgentRunOutcome} == {
        "completed",
        "escalated",
        "failed_timeout",
        "failed_budget",
        "failed_error",
        "killed_by_switch",
    }


def test_semantic_review_item_types_are_declared() -> None:
    assert {
        ReviewItemType.NEWS_STATUS_UNCORROBORATED.value,
        ReviewItemType.MULTI_TENURE_REVIEW.value,
        ReviewItemType.PROJECT_CANCELLATION_REVIEW.value,
    } == {
        "news_status_uncorroborated",
        "multi_tenure_review",
        "project_cancellation_review",
    }


def test_system_alert_indexes_are_declared() -> None:
    index_names = {index.name for index in SystemAlert.__table__.indexes}

    assert "uq_system_alerts_active_key_scope" in index_names
    assert "ix_system_alerts_active" not in index_names


def test_resolution_log_and_developer_alias_indexes_are_declared() -> None:
    resolution_index_names = {index.name for index in ResolutionLog.__table__.indexes}
    developer_alias_index_names = {index.name for index in DeveloperAlias.__table__.indexes}

    assert "ix_resolution_log_project_id_created_at" in resolution_index_names
    assert "ix_developer_alias_developer_id" in developer_alias_index_names
