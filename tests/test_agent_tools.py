from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE
from tcg_pipeline.agents.project_tools import (
    GET_PROJECT_STATE_OUTPUT_TOKEN_BUDGET,
    build_agent_tool_registry,
    handle_get_project_state,
)
from tcg_pipeline.agents.runner import AgentRunRequest, IntakeRecord
from tcg_pipeline.agents.tools import (
    AgentTool,
    AgentToolError,
    AgentToolRegistry,
    AgentToolResult,
)
from tcg_pipeline.db.models import (
    AgeRestriction,
    Evidence,
    PipelineStatus,
    ProductType,
    Project,
    ResolutionLog,
    StatusConfidence,
)
from tcg_pipeline.settings import Settings


def _tool(name: str = "search_articles_similar", *, budget: int = 1000) -> AgentTool:
    return AgentTool(
        name=name,
        description="Test tool",
        input_schema={"type": "object", "properties": {}},
        output_token_budget=budget,
        handler=lambda _args, _request: AgentToolResult(
            payload={"items": [{"title": "A" * 200}]},
            summary="summary",
            total_results=1,
        ),
    )


def test_tool_registry_exposes_only_profile_allowed_tools() -> None:
    registry = AgentToolRegistry(
        {
            "search_articles_similar": _tool("search_articles_similar"),
            "not_allowed": _tool("not_allowed"),
        }
    )

    specs = registry.tool_specs_for_profile(NEWS_AGENT_PROFILE)

    assert [spec["name"] for spec in specs] == ["search_articles_similar"]


def test_tool_dispatch_enforces_allowed_tool_set() -> None:
    registry = AgentToolRegistry({"not_allowed": _tool("not_allowed")})

    with pytest.raises(AgentToolError, match="not allowed"):
        registry.dispatch(
            tool_name="not_allowed",
            tool_input={},
            profile=NEWS_AGENT_PROFILE,
            request=None,
        )


def test_tool_dispatch_truncates_over_budget_payload() -> None:
    profile = replace(NEWS_AGENT_PROFILE, allowed_tools=frozenset({"search_articles_similar"}))
    registry = AgentToolRegistry({"search_articles_similar": _tool(budget=4)})

    result = registry.dispatch(
        tool_name="search_articles_similar",
        tool_input={"query_text": "Example"},
        profile=profile,
        request=None,
    )

    assert result.content["truncated"] is True
    assert result.content["total_results"] == 1
    assert result.content["hint"]
    assert result.summary["truncated"] is True


def test_default_tool_registry_includes_get_project_state() -> None:
    registry = build_agent_tool_registry()

    specs = registry.tool_specs_for_profile(NEWS_AGENT_PROFILE)

    assert [spec["name"] for spec in specs] == ["get_project_state"]
    assert specs[0]["input_schema"]["required"] == ["project_id"]


def test_get_project_state_requires_session_factory() -> None:
    request = AgentRunRequest(
        intake=IntakeRecord(
            source_type="news_article",
            intake_record_id=str(uuid.uuid4()),
            extraction_id=uuid.uuid4(),
        ),
        matcher_results=(),
        trigger_reasons=("new_candidate",),
        profile=NEWS_AGENT_PROFILE,
    )

    with pytest.raises(AgentToolError, match="requires a session_factory"):
        handle_get_project_state({"project_id": str(uuid.uuid4())}, request)


def test_get_project_state_reads_project_resolution_context(
    postgres_session: Session,
) -> None:
    _ensure_project_state_views(postgres_session)
    project = Project(
        canonical_address="123 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["123 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Main Street Apartments",
        developer="HPG",
        pipeline_status=PipelineStatus.PROPOSED,
        total_units=100,
        product_type=ProductType.APARTMENT,
        age_restriction=AgeRestriction.UNKNOWN,
    )
    postgres_session.add(project)
    postgres_session.flush()
    evidence = Evidence(
        project_id=project.id,
        source_type="news_article",
        source_tier=2,
        ingest_method="test",
        collected_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 5),
        extracted_fields={"total_units": {"value": 125, "confidence": "high"}},
        notes="Article reported 125 units.",
    )
    postgres_session.add(evidence)
    postgres_session.flush()
    postgres_session.add(
        ResolutionLog(
            project_id=project.id,
            field="total_units",
            current_value=100,
            resolved_value=125,
            evidence_ids=[evidence.id],
            rule_applied="higher_tier",
            confidence=StatusConfidence.HIGH,
            created_at=datetime(2026, 5, 5, 12, 1, tzinfo=UTC),
        )
    )
    postgres_session.flush()
    factory = sessionmaker(
        bind=postgres_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    request = AgentRunRequest(
        intake=IntakeRecord(
            source_type="news_article",
            intake_record_id=str(uuid.uuid4()),
            extraction_id=uuid.uuid4(),
        ),
        matcher_results=(),
        trigger_reasons=("material_contradiction",),
        profile=NEWS_AGENT_PROFILE,
        session_factory=factory,
        settings=Settings(agent_enabled_for_news=True),
    )

    result = handle_get_project_state({"project_id": str(project.id)}, request)

    assert result.total_results == 1
    assert result.payload["project"]["project_name"] == "Main Street Apartments"
    assert result.payload["project"]["pipeline_status"] == PipelineStatus.PROPOSED.value
    assert result.payload["latest_evidence"]["source_type"] == "news_article"
    assert result.payload["confidence_breakdown"] == {"high": 1}
    field = result.payload["fields"][0]
    assert field["field_name"] == "total_units"
    assert field["value"] == 125
    assert field["rule"] == "higher_tier"
    assert field["evidence"][0]["source_type"] == "news_article"
    assert str(evidence.id) in field["evidence_ids"]
    assert GET_PROJECT_STATE_OUTPUT_TOKEN_BUDGET == 1500


def _ensure_project_state_views(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    missing_tables = [
        table_name
        for table_name in ("projects", "evidence", "resolution_log")
        if not inspector.has_table(table_name)
    ]
    view_names = set(inspector.get_view_names())
    missing_views = [
        view_name
        for view_name in ("project_field_resolution", "project_latest_evidence")
        if view_name not in view_names
    ]
    missing = missing_tables + missing_views
    if missing:
        pytest.skip(f"Apply Phase B read-model migrations before running tool tests: {missing}")
