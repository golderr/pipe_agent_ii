from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE, PERMIT_AGENT_PROFILE
from tcg_pipeline.agents.project_tools import (
    GET_PROJECT_STATE_OUTPUT_TOKEN_BUDGET,
    handle_get_project_state,
    handle_search_projects,
)
from tcg_pipeline.agents.registry import build_agent_tool_registry
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
    NewsArticle,
    NewsArticleChunk,
    NewsExtraction,
    NewsMatchStatus,
    NewsProjectReference,
    NewsSource,
    PipelineStatus,
    ProductType,
    Project,
    ResolutionLog,
    StatusConfidence,
)
from tcg_pipeline.news.embeddings import EmbeddingResponse
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

    assert [spec["name"] for spec in specs] == [
        "get_article_body",
        "get_permits_for_project",
        "get_project_state",
        "search_articles_similar",
        "search_projects",
    ]
    spec_by_name = {spec["name"]: spec for spec in specs}
    assert spec_by_name["get_project_state"]["input_schema"]["required"] == ["project_id"]
    assert spec_by_name["get_permits_for_project"]["input_schema"]["required"] == [
        "project_id"
    ]
    assert spec_by_name["get_article_body"]["input_schema"]["required"] == ["article_id"]
    assert spec_by_name["search_articles_similar"]["input_schema"]["required"] == ["query_text"]
    assert "query_text" in spec_by_name["search_projects"]["input_schema"]["properties"]


def test_default_tool_registry_includes_permit_profile_tools() -> None:
    registry = build_agent_tool_registry()

    specs = registry.tool_specs_for_profile(PERMIT_AGENT_PROFILE)

    assert [spec["name"] for spec in specs] == [
        "get_articles_about_parcel_or_address",
        "get_permits_for_parcel",
        "get_permits_for_project",
        "get_project_state",
        "search_projects",
    ]
    spec_by_name = {spec["name"]: spec for spec in specs}
    assert spec_by_name["get_articles_about_parcel_or_address"]["input_schema"]["anyOf"] == [
        {"required": ["parcel_id"]},
        {"required": ["address"]},
    ]
    assert spec_by_name["get_permits_for_parcel"]["input_schema"]["required"] == [
        "parcel_id"
    ]
    assert spec_by_name["get_permits_for_project"]["input_schema"]["required"] == [
        "project_id"
    ]


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


def test_search_projects_returns_compact_registry_candidates(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="901 TEST AGENT LANE LOS ANGELES CA 90013",
        raw_addresses=["901 Test Agent Ln"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Agent Search Residences",
        developer="Agent Search Sponsor",
        pipeline_status=PipelineStatus.UNDER_CONSTRUCTION,
        total_units=98,
        affordable_units=97,
        market_rate_units=1,
        workforce_units=0,
        product_type=ProductType.APARTMENT,
    )
    other_project = Project(
        canonical_address="700 SOUTH FLOWER STREET LOS ANGELES CA 90017",
        raw_addresses=["700 S Flower St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Flower Tower",
        developer="Other Sponsor",
        pipeline_status=PipelineStatus.PROPOSED,
        total_units=200,
    )
    postgres_session.add_all([project, other_project])
    postgres_session.flush()
    request = _agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="search_projects",
        tool_input={
            "query_text": "Agent Search Residences 901 Test Agent Lane",
            "address": "901 Test Agent Lane",
            "project_name": "Agent Search Residences",
            "developer": "Agent Search Sponsor",
        },
        profile=NEWS_AGENT_PROFILE,
        request=request,
    )

    assert result.content["total_available"] >= 1
    assert result.content["matches"][0]["project_id"] == str(project.id)
    assert result.content["matches"][0]["canonical_address"] == project.canonical_address
    assert result.content["matches"][0]["workforce_units"] == 0
    assert result.content["matches"][0]["score"] >= 0.9
    assert "address_match" in result.content["matches"][0]["reasons"]


def test_search_projects_requires_session_factory() -> None:
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
        handle_search_projects({"query_text": "501 E. 5th"}, request)


def test_get_article_body_returns_truncated_body(postgres_session: Session) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _news_source()
    article = _news_article(source, body_text="Sentence one.\n\nSentence two. " + "A" * 100)
    postgres_session.add_all([source, article])
    postgres_session.flush()
    request = _agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_article_body",
        tool_input={"article_id": str(article.id), "max_chars": 32},
        profile=NEWS_AGENT_PROFILE,
        request=request,
    )

    assert result.content["article_id"] == str(article.id)
    assert result.content["source_slug"] == source.slug
    assert result.content["truncated"] is True
    assert result.content["body_text"].endswith("...")
    assert "\n" not in result.content["body_text"]


def test_search_articles_similar_returns_compact_chunk_results(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _news_source()
    article = _news_article(source, title="Accepted Tower proposed")
    project = Project(
        canonical_address="123 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["123 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Accepted Tower",
        developer="HPG",
        pipeline_status=PipelineStatus.PROPOSED,
        total_units=120,
    )
    postgres_session.add_all([source, article, project])
    postgres_session.flush()
    extraction = _news_extraction(article)
    postgres_session.add(extraction)
    postgres_session.flush()
    reference = NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=0,
        candidate_name="Accepted Tower",
        candidate_address="123 Main St",
        candidate_city="Los Angeles",
        candidate_developer="HPG",
        candidate_confidence="high",
        match_status=NewsMatchStatus.CONFIRMED.value,
    )
    postgres_session.add(reference)
    postgres_session.flush()
    evidence = Evidence(
        project_id=project.id,
        source_type="news_article",
        source_record_id=str(reference.id),
        source_tier=2,
        ingest_method="test",
        collected_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 5),
        extracted_fields={"total_units": {"value": 120, "confidence": "high"}},
        notes="Article reported Accepted Tower.",
    )
    chunk = NewsArticleChunk(
        article_id=article.id,
        reference_index=0,
        chunk_text="Accepted Tower would include 120 apartments near Main Street." * 5,
        chunk_offset_start=0,
        chunk_offset_end=80,
        embedding=[1.0] + [0.0] * 1535,
        embedded_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        model="text-embedding-3-small",
        gate_source="review_accept",
    )
    whole_article_chunk = NewsArticleChunk(
        article_id=article.id,
        reference_index=None,
        chunk_text="Whole article context should be excluded by default.",
        embedding=[1.0] + [0.0] * 1535,
        embedded_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        model="text-embedding-3-small",
        gate_source="review_accept",
    )
    postgres_session.add_all([evidence, chunk, whole_article_chunk])
    postgres_session.flush()
    request = _agent_request(postgres_session, embedding_client=_FakeEmbeddingClient())
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="search_articles_similar",
        tool_input={"query_text": "Accepted Tower Main Street", "top_k": 5},
        profile=NEWS_AGENT_PROFILE,
        request=request,
    )

    assert result.content["query_embedding_cost_accounting"] == "ignored_negligible"
    assert result.content["total_available"] >= 1
    match = next(
        item for item in result.content["matches"] if item["article_id"] == str(article.id)
    )
    assert match["article_id"] == str(article.id)
    assert match["reference_index"] == 0
    assert match["similarity"] == 1
    assert len(match["excerpt"]) <= 200
    assert match["match_status"] == NewsMatchStatus.CONFIRMED.value
    assert match["candidate_city"] == "Los Angeles"
    assert match["matched_project_id"] == str(project.id)
    assert match["matched_evidence_id"] == str(evidence.id)
    assert result.summary["tool"] == "search_articles_similar"


def test_search_articles_similar_filters_by_published_after(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _news_source()
    old_article = _news_article(
        source,
        title="Old accepted article",
        published_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    )
    fresh_article = _news_article(
        source,
        title="Fresh accepted article",
        published_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )
    postgres_session.add_all([source, old_article, fresh_article])
    postgres_session.flush()
    postgres_session.add_all(
        [
            NewsArticleChunk(
                article_id=old_article.id,
                reference_index=0,
                chunk_text="Accepted Tower older article near Main Street.",
                embedding=[1.0] + [0.0] * 1535,
                embedded_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
                model="text-embedding-3-small",
                gate_source="review_accept",
            ),
            NewsArticleChunk(
                article_id=fresh_article.id,
                reference_index=0,
                chunk_text="Accepted Tower fresh article near Main Street.",
                embedding=[1.0] + [0.0] * 1535,
                embedded_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
                model="text-embedding-3-small",
                gate_source="review_accept",
            ),
        ]
    )
    postgres_session.flush()
    request = _agent_request(postgres_session, embedding_client=_FakeEmbeddingClient())
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="search_articles_similar",
        tool_input={
            "query_text": "Accepted Tower Main Street",
            "published_after": "2026-05-01",
            "top_k": 10,
        },
        profile=NEWS_AGENT_PROFILE,
        request=request,
    )

    article_ids = {match["article_id"] for match in result.content["matches"]}
    assert str(fresh_article.id) in article_ids
    assert str(old_article.id) not in article_ids
    assert result.content["published_after"] == "2026-05-01 00:00:00+00:00"
    assert result.content["total_available"] == 1


def test_search_articles_similar_rejects_invalid_published_after() -> None:
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
    registry = build_agent_tool_registry()

    with pytest.raises(AgentToolError, match="published_after"):
        registry.dispatch(
            tool_name="search_articles_similar",
            tool_input={
                "query_text": "Accepted Tower Main Street",
                "published_after": "not-a-date",
            },
            profile=NEWS_AGENT_PROFILE,
            request=request,
        )


def test_search_articles_similar_requires_query_text() -> None:
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
    registry = build_agent_tool_registry()

    with pytest.raises(AgentToolError, match="query_text"):
        registry.dispatch(
            tool_name="search_articles_similar",
            tool_input={"query_text": ""},
            profile=NEWS_AGENT_PROFILE,
            request=request,
        )


def test_get_permits_for_project_returns_ladbs_evidence(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="1200 PERMIT TOOL WAY LOS ANGELES CA 90012",
        raw_addresses=["1200 Permit Tool Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Permit Tool Apartments",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()
    permit = Evidence(
        project_id=project.id,
        source_type="ladbs_permit",
        source_tier=1,
        ingest_method="test",
        source_record_id="24010-10000-00001",
        collected_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 10),
        raw_data={"permit_nbr": "24010-10000-00001", "apn": "5146013024"},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={
            "apn": {"value": "5146013024", "confidence": None},
            "permit_type": {"value": "Bldg-New", "confidence": None},
            "permit_sub_type": {"value": "Apartment", "confidence": None},
            "permit_issue_date": {"value": "2026-05-10", "confidence": None},
            "description": {
                "value": "Construct new six-story apartment building.",
                "confidence": None,
            },
            "valuation": {"value": 10_000_000, "confidence": None},
            "total_units": {"value": 118, "confidence": None},
        },
    )
    inspection = Evidence(
        project_id=project.id,
        source_type="ladbs_inspection",
        source_tier=1,
        ingest_method="test",
        source_record_id="inspection-1",
        collected_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 11),
        raw_data={"permit_nbr": "24010-10000-00001"},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={
            "inspection_date": {"value": "2026-05-11", "confidence": None},
            "status_evidence_type": {
                "value": "substantive_inspection",
                "confidence": None,
            },
            "status_desc": {"value": "Approved", "confidence": None},
        },
    )
    postgres_session.add_all([permit, inspection])
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_permits_for_project",
        tool_input={"project_id": str(project.id), "limit": 5},
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    assert result.content["project_id"] == str(project.id)
    assert result.content["total_returned"] == 2
    first = result.content["permits"][0]
    assert first["source_type"] == "ladbs_inspection"
    assert first["permit_number"] == "24010-10000-00001"
    assert first["status_evidence_type"] == "substantive_inspection"
    second = result.content["permits"][1]
    assert second["source_type"] == "ladbs_permit"
    assert second["permit_type"] == "Bldg-New"
    assert second["total_units"] == 118
    assert second["valuation"] == 10_000_000


def test_get_permits_for_parcel_matches_raw_or_mapped_apn(
    postgres_session: Session,
) -> None:
    evidence = Evidence(
        project_id=None,
        source_type="ladbs_permit",
        source_tier=1,
        ingest_method="test",
        source_record_id="24010-10000-00002",
        collected_at=datetime(2026, 5, 12, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 12),
        raw_data={"permit_nbr": "24010-10000-00002", "apn": "5146-013-024"},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={
            "apn": {"value": "5146013024", "confidence": None},
            "permit_type": {"value": "Bldg-New", "confidence": None},
            "description": {"value": "New apartment building.", "confidence": None},
        },
    )
    unrelated = Evidence(
        source_type="ladbs_permit",
        source_tier=1,
        ingest_method="test",
        source_record_id="24010-10000-00003",
        collected_at=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 13),
        raw_data={"permit_nbr": "24010-10000-00003", "apn": "9999999999"},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={"apn": {"value": "9999999999", "confidence": None}},
    )
    postgres_session.add_all([evidence, unrelated])
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_permits_for_parcel",
        tool_input={"parcel_id": "5146-013-024"},
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    assert result.content["normalized_parcel_id"] == "5146013024"
    assert result.content["total_returned"] == 1
    match = result.content["permits"][0]
    assert match["source_record_id"] == "24010-10000-00002"
    assert match["apn"] == "5146013024"
    assert match["description"] == "New apartment building."


def test_get_permits_for_project_clamps_requested_limit_without_truncating(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="1225 PERMIT LIMIT WAY LOS ANGELES CA 90012",
        raw_addresses=["1225 Permit Limit Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Permit Limit Apartments",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=100,
    )
    postgres_session.add(project)
    postgres_session.flush()
    for index in range(25):
        postgres_session.add(
            Evidence(
                project_id=project.id,
                source_type="ladbs_permit",
                source_tier=1,
                ingest_method="test",
                source_record_id=f"24010-10000-{index:05d}",
                collected_at=datetime(2026, 5, 12, 12, index % 60, tzinfo=UTC),
                evidence_date=date(2026, 5, 12),
                raw_data={
                    "permit_nbr": f"24010-10000-{index:05d}",
                    "apn": "5146013024",
                },
                raw_data_hash=uuid.uuid4().hex,
                extracted_fields={
                    "apn": {"value": "5146013024", "confidence": None},
                    "permit_type": {"value": "Bldg-New", "confidence": None},
                    "permit_sub_type": {"value": "Apartment", "confidence": None},
                    "permit_issue_date": {"value": "2026-05-12", "confidence": None},
                    "description": {
                        "value": (
                            "Construct new seven-story mixed-use apartment building "
                            "with residential units, ground-floor retail, and "
                            "subterranean parking."
                        ),
                        "confidence": None,
                    },
                    "valuation": {"value": 12_500_000 + index, "confidence": None},
                    "total_units": {"value": 100 + index, "confidence": None},
                },
            )
        )
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_permits_for_project",
        tool_input={"project_id": str(project.id), "limit": 25},
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    assert result.content.get("truncated") is not True
    assert result.content["limit"] == 10
    assert result.content["total_returned"] == 10
    assert len(result.content["permits"]) == 10


def test_get_permits_payload_prefers_extracted_values_before_raw_fallback(
    postgres_session: Session,
) -> None:
    evidence = Evidence(
        source_type="ladbs_permit",
        source_tier=1,
        ingest_method="test",
        source_record_id="raw-source-record",
        collected_at=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 14),
        raw_data={
            "pcis_permit": "RAW-PCIS",
            "permit_nbr": "RAW-PERMIT-NBR",
            "apn": "RAW-APN",
            "work_desc": "Raw description",
        },
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={
            "permit_number": {"value": "EXTRACTED-PERMIT", "confidence": None},
            "apn": {"value": "5146013024", "confidence": None},
            "description": {"value": "Extracted description", "confidence": None},
        },
    )
    postgres_session.add(evidence)
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_permits_for_parcel",
        tool_input={"parcel_id": "5146013024"},
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    match = result.content["permits"][0]
    assert match["permit_number"] == "EXTRACTED-PERMIT"
    assert match["apn"] == "5146013024"
    assert match["description"] == "Extracted description"


def test_get_articles_about_parcel_or_address_uses_apn_project_crosswalk(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _news_source()
    article = _news_article(
        source,
        title="Permit Crosswalk Apartments advance",
        body_text="Permit Crosswalk Apartments received coverage near the permit parcel.",
    )
    project = Project(
        canonical_address="1500 PERMIT CROSSWALK AVENUE LOS ANGELES CA 90012",
        raw_addresses=["1500 Permit Crosswalk Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Permit Crosswalk Apartments",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=120,
        lat=34.0500,
        lng=-118.2500,
    )
    postgres_session.add_all([source, article, project])
    postgres_session.flush()
    permit_evidence = Evidence(
        project_id=project.id,
        source_type="ladbs_permit",
        source_tier=1,
        ingest_method="test",
        source_record_id="24010-10000-01000",
        collected_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 10),
        raw_data={"permit_nbr": "24010-10000-01000", "apn": "5146-013-024"},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={"apn": {"value": "5146013024", "confidence": None}},
    )
    extraction = _news_extraction(article)
    postgres_session.add_all([permit_evidence, extraction])
    postgres_session.flush()
    reference = NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=0,
        candidate_name="Permit Crosswalk Apartments",
        candidate_address="1500 Permit Crosswalk Ave",
        candidate_city="Los Angeles",
        candidate_developer="Crosswalk Sponsor",
        candidate_unit_total=120,
        candidate_confidence="high",
        match_status=NewsMatchStatus.CONFIRMED.value,
        matched_project_id=project.id,
        passage_excerpts=[
            {
                "field": "candidate_name",
                "value": "Permit Crosswalk Apartments",
                "passage": "Permit Crosswalk Apartments would bring 120 homes.",
            }
        ],
    )
    postgres_session.add(reference)
    postgres_session.flush()
    news_evidence = Evidence(
        project_id=project.id,
        source_type="news_article",
        source_tier=2,
        ingest_method="test",
        source_record_id=str(reference.id),
        collected_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        evidence_date=date(2026, 5, 11),
        raw_data={"article_id": str(article.id)},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={"total_units": {"value": 120, "confidence": "high"}},
        notes="Accepted news coverage for the crosswalk project.",
    )
    postgres_session.add(news_evidence)
    postgres_session.flush()
    reference.matched_evidence_id = news_evidence.id
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_articles_about_parcel_or_address",
        tool_input={
            "parcel_id": "5146-013-024",
            "address": "1500 Permit Crosswalk Avenue, Los Angeles, CA",
        },
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    assert result.content.get("truncated") is not True
    assert result.content["normalized_parcel_id"] == "5146013024"
    assert result.content["total_available"] == 1
    assert result.content["total_returned"] == 1
    match = result.content["matches"][0]
    assert match["match_basis"] == "parcel_project_news_evidence"
    assert match["reference_id"] == str(reference.id)
    assert "evidence_id" not in match
    assert "matched_evidence_id" not in match
    assert "candidate_unit_total" not in match
    assert "distance_feet" not in match
    assert match["excerpt"] == "Permit Crosswalk Apartments would bring 120 homes."


def test_get_articles_about_parcel_or_address_matches_normalized_reference_address(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _news_source()
    article = _news_article(
        source,
        title="Address-only reference",
        body_text="The address-only project was mentioned without a matched project.",
    )
    postgres_session.add_all([source, article])
    postgres_session.flush()
    extraction = _news_extraction(article)
    postgres_session.add(extraction)
    postgres_session.flush()
    reference = NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=0,
        candidate_name="Address Only Residences",
        candidate_address="777 Permit News Ave",
        candidate_city="Los Angeles",
        candidate_developer="Address Sponsor",
        candidate_confidence="medium",
        match_status=NewsMatchStatus.PENDING.value,
        passage_excerpts=[
            {
                "field": "candidate_address",
                "value": "777 Permit News Ave",
                "passage": "The project at 777 Permit News Ave remains in review.",
            }
        ],
    )
    postgres_session.add(reference)
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_articles_about_parcel_or_address",
        tool_input={"address": "777 Permit News Avenue, Los Angeles, CA"},
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    assert result.content["normalized_address"] == "777 PERMIT NEWS AVENUE LOS ANGELES CA"
    assert result.content["total_returned"] == 1
    match = result.content["matches"][0]
    assert match["match_basis"] == "address_reference_exact"
    assert match["reference_id"] == str(reference.id)
    assert "evidence_id" not in match
    assert match["excerpt"] == "The project at 777 Permit News Ave remains in review."


def test_get_articles_about_parcel_or_address_clamps_limit_under_budget(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _news_source()
    project = Project(
        canonical_address="1600 PERMIT BUDGET AVENUE LOS ANGELES CA 90012",
        raw_addresses=["1600 Permit Budget Ave"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Permit Budget Apartments",
        pipeline_status=PipelineStatus.APPROVED,
        total_units=150,
    )
    postgres_session.add_all([source, project])
    postgres_session.flush()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="ladbs_permit",
            source_tier=1,
            ingest_method="test",
            source_record_id="24010-10000-02000",
            collected_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
            evidence_date=date(2026, 5, 10),
            raw_data={"permit_nbr": "24010-10000-02000", "apn": "5146013024"},
            raw_data_hash=uuid.uuid4().hex,
            extracted_fields={"apn": {"value": "5146013024", "confidence": None}},
        )
    )
    for index in range(15):
        article = _news_article(
            source,
            title=f"Budget context article {index:02d}",
            body_text="Body context " * 60,
            published_at=datetime(2026, 5, 1, 12, index, tzinfo=UTC),
        )
        postgres_session.add(article)
        postgres_session.flush()
        extraction = _news_extraction(article)
        postgres_session.add(extraction)
        postgres_session.flush()
        reference = NewsProjectReference(
            article_id=article.id,
            extraction_id=extraction.id,
            reference_index=0,
            candidate_name=f"Permit Budget Apartments phase {index:02d}",
            candidate_address="1600 Permit Budget Ave",
            candidate_city="Los Angeles",
            candidate_developer="Budget Sponsor",
            candidate_unit_total=150 + index,
            candidate_confidence="high",
            match_status=NewsMatchStatus.CONFIRMED.value,
            matched_project_id=project.id,
            passage_excerpts=[
                {
                    "field": "candidate_name",
                    "value": "Permit Budget Apartments",
                    "passage": (
                        "Permit Budget Apartments was described with enough contextual "
                        "language to exceed the per-match excerpt cap. "
                        + ("additional supporting phrase " * 12)
                    ),
                }
            ],
        )
        postgres_session.add(reference)
        postgres_session.flush()
        news_evidence = Evidence(
            project_id=project.id,
            source_type="news_article",
            source_tier=2,
            ingest_method="test",
            source_record_id=str(reference.id),
            collected_at=datetime(2026, 5, 11, 12, index, tzinfo=UTC),
            evidence_date=date(2026, 5, 11),
            raw_data={"article_id": str(article.id)},
            raw_data_hash=uuid.uuid4().hex,
            extracted_fields={"total_units": {"value": 150 + index, "confidence": "high"}},
            notes="Accepted news coverage for budget testing.",
        )
        postgres_session.add(news_evidence)
        postgres_session.flush()
        reference.matched_evidence_id = news_evidence.id
    postgres_session.flush()
    request = _permit_agent_request(postgres_session)
    registry = build_agent_tool_registry()

    result = registry.dispatch(
        tool_name="get_articles_about_parcel_or_address",
        tool_input={"parcel_id": "5146013024", "limit": 15},
        profile=PERMIT_AGENT_PROFILE,
        request=request,
    )

    assert result.content.get("truncated") is not True
    assert result.content["limit"] == 10
    assert result.content["total_available"] == 15
    assert result.content["total_returned"] == 10
    assert len(result.content["matches"]) == 10
    first_match = result.content["matches"][0]
    assert "evidence_id" not in first_match
    assert "matched_evidence_id" not in first_match
    assert "candidate_unit_total" not in first_match
    assert len(first_match["excerpt"]) <= 140


def test_get_permits_for_parcel_requires_session_factory() -> None:
    request = AgentRunRequest(
        intake=IntakeRecord(
            source_type="ladbs_permit",
            intake_record_id="24010-10000-00002",
        ),
        matcher_results=(),
        trigger_reasons=("new_candidate",),
        profile=PERMIT_AGENT_PROFILE,
    )
    registry = build_agent_tool_registry()

    with pytest.raises(AgentToolError, match="requires a session_factory"):
        registry.dispatch(
            tool_name="get_permits_for_parcel",
            tool_input={"parcel_id": "5146013024"},
            profile=PERMIT_AGENT_PROFILE,
            request=request,
        )


def test_get_articles_about_parcel_or_address_requires_input() -> None:
    request = _permit_agent_request_without_session()
    registry = build_agent_tool_registry()

    with pytest.raises(AgentToolError, match="parcel_id or address"):
        registry.dispatch(
            tool_name="get_articles_about_parcel_or_address",
            tool_input={},
            profile=PERMIT_AGENT_PROFILE,
            request=request,
        )


def test_get_articles_about_parcel_or_address_requires_session_factory() -> None:
    request = _permit_agent_request_without_session()
    registry = build_agent_tool_registry()

    with pytest.raises(AgentToolError, match="requires a session_factory"):
        registry.dispatch(
            tool_name="get_articles_about_parcel_or_address",
            tool_input={"parcel_id": "5146013024"},
            profile=PERMIT_AGENT_PROFILE,
            request=request,
        )


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


def _ensure_agent1_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    missing = [
        table_name
        for table_name in (
            "projects",
            "evidence",
            "news_sources",
            "news_articles",
            "news_extractions",
            "news_project_references",
            "news_article_chunks",
        )
        if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply AGENT.1 migrations before running news tool tests: {missing}")


def _agent_request(
    postgres_session: Session,
    *,
    embedding_client=None,
) -> AgentRunRequest:
    factory = sessionmaker(
        bind=postgres_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    return AgentRunRequest(
        intake=IntakeRecord(
            source_type="news_article",
            intake_record_id=str(uuid.uuid4()),
            extraction_id=uuid.uuid4(),
        ),
        matcher_results=(),
        trigger_reasons=("new_candidate",),
        profile=NEWS_AGENT_PROFILE,
        session_factory=factory,
        settings=Settings(agent_enabled_for_news=True, openai_api_key="test"),
        embedding_client=embedding_client,
    )


def _permit_agent_request(postgres_session: Session) -> AgentRunRequest:
    factory = sessionmaker(
        bind=postgres_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    return AgentRunRequest(
        intake=IntakeRecord(
            source_type="ladbs_permit",
            intake_record_id="24010-10000-00001",
        ),
        matcher_results=(),
        trigger_reasons=("new_candidate",),
        profile=PERMIT_AGENT_PROFILE,
        session_factory=factory,
        settings=Settings(agent_enabled_for_permits=True),
    )


def _permit_agent_request_without_session() -> AgentRunRequest:
    return AgentRunRequest(
        intake=IntakeRecord(
            source_type="ladbs_permit",
            intake_record_id="24010-10000-00001",
        ),
        matcher_results=(),
        trigger_reasons=("new_candidate",),
        profile=PERMIT_AGENT_PROFILE,
        settings=Settings(agent_enabled_for_permits=True),
    )


def _news_source() -> NewsSource:
    slug = f"agent-tool-{uuid.uuid4().hex}"
    return NewsSource(
        id=uuid.uuid4(),
        slug=slug,
        name="Agent Tool Test",
        base_url="https://example.com",
        collector_class="TestCollector",
    )


def _news_article(
    source: NewsSource,
    *,
    title: str = "Agent article",
    body_text: str = "Body",
    published_at: datetime | None = None,
):
    return NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/{uuid.uuid4().hex}",
        url_original="https://example.com/original",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status="fetched",
        triage_status="relevant",
        body_text=body_text,
        body_text_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        title=title,
        published_at=published_at or datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        ingest_method="test",
    )


def _news_extraction(article: NewsArticle) -> NewsExtraction:
    return NewsExtraction(
        article_id=article.id,
        pass_name="extraction",
        triggered_by="test",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash=uuid.uuid4().hex,
        model="claude-opus-4-7",
        output_json={"project_references": []},
    )


class _FakeEmbeddingClient:
    model = "text-embedding-3-small"
    provider = "openai"

    def embed_texts(self, texts):  # type: ignore[no-untyped-def]
        assert texts == ["Accepted Tower Main Street"]
        return EmbeddingResponse(
            embeddings=([1.0] + [0.0] * 1535,),
            model=self.model,
            provider=self.provider,
            input_tokens=5,
            latency_ms=1,
        )
