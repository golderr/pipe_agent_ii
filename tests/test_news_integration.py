from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.runner import AgentClientResult, AgentRunRequest
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    Evidence,
    IdentifierType,
    Jurisdiction,
    Market,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsMatchStatus,
    NewsProjectReference,
    NewsSource,
    NewsTriageStatus,
    PipelineStatus,
    Priority,
    Project,
    ProjectIdentifier,
    ReviewItem,
    ReviewItemType,
    SourceRun,
    StatusConfidence,
)
from tcg_pipeline.db.researcher_overrides import upsert_researcher_overrides
from tcg_pipeline.db.review_workflow import accept_review_item
from tcg_pipeline.matching.news_matcher import NewsMatchResult, match_news_reference
from tcg_pipeline.matching.normalizer import normalize_address
from tcg_pipeline.news.extraction import NewsExtractionRunResult
from tcg_pipeline.news.integration import (
    _ConfirmedReference,
    _field_reference_context,
    _ProjectIntegrationContext,
    news_article_agent_trigger_reasons,
    run_news_integration_for_article,
)
from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC, LLMUsage
from tcg_pipeline.resolution.fields import FieldResolution
from tcg_pipeline.semantic.news.pass2c import RenderedInterpretPrompt, SemanticLLMResponse
from tcg_pipeline.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def default_news_integration_tests_to_legacy_semantic(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NEWS_USE_LEGACY_SEMANTIC", "true")
    monkeypatch.setenv("AGENT_ENABLED_FOR_NEWS", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeNewsAgentClient:
    provider = LLM_PROVIDER_ANTHROPIC
    model = DEFAULT_EXTRACTION_MODEL
    prompt_version = "agent_news_v1"

    def __init__(self, verdict: dict) -> None:
        self.verdict = verdict
        self.requests: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        self.requests.append(request)
        return AgentClientResult(
            outcome=AgentRunOutcome.COMPLETED.value,
            usage=LLMUsage(
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=0,
                output_tokens=25,
            ),
            latency_ms=100,
            reasoning_trace="Agent reviewed deterministic matcher output against available tools.",
            evidence_consulted=[
                {
                    "source_type": "news_article",
                    "record_id": request.intake.intake_record_id,
                    "role": "primary",
                }
            ],
            tool_calls_summary=[],
            agent_revised_verdict=self.verdict,
        )


class FakeSemanticClient:
    provider = LLM_PROVIDER_ANTHROPIC
    model = DEFAULT_EXTRACTION_MODEL

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[RenderedInterpretPrompt] = []

    def interpret(self, prompt: RenderedInterpretPrompt) -> SemanticLLMResponse:
        self.prompts.append(prompt)
        return SemanticLLMResponse(
            payload=self.payload,
            text="{}",
            model=self.model,
            provider=self.provider,
            usage=LLMUsage(
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=20,
                output_tokens=30,
            ),
            latency_ms=123,
            stop_reason=None,
        )


def test_news_matcher_confirms_identifier_and_ignores_invalid_registry_hint(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address="100 MATCHER WAY LOS ANGELES CA 90012",
        project_name="Matcher Tower",
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        ProjectIdentifier(
            project_id=project.id,
            identifier_type=IdentifierType.CASE_NUMBER,
            value="CPC-2026-101",
        )
    )
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Other Name",
                candidate_identifiers={"case_number": ["CPC-2026-101"]},
                registry_project_id=str(uuid.uuid4()),
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    postgres_session.flush()

    match = match_news_reference(postgres_session, article=article, reference=reference)

    assert match.status == NewsMatchStatus.CONFIRMED
    assert match.project_id == project.id
    assert match.confidence == 0.97
    assert "ignored_registry_project_id" in match.diagnostics


def test_news_integration_writes_confirmed_evidence_and_per_field_review_items(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("1234 Sunset Boulevard, Los Angeles, CA 90026")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Helio",
        developer="Atlas Development",
        total_units=100,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Helio",
                candidate_address="1234 Sunset Boulevard, Los Angeles, CA 90026",
                candidate_developer="Atlas Development",
                candidate_unit_total=140,
                candidate_unit_workforce=16,
                passage_excerpts=[
                    {
                        "field": "candidate_unit_total",
                        "value": 140,
                        "passage": "Atlas broke ground on the 140-unit Helio project.",
                        "offset_start": 27,
                        "offset_end": 35,
                    },
                    {
                        "field": "candidate_unit_workforce",
                        "value": 16,
                        "passage": "The plans include 16 workforce units.",
                        "offset_start": 36,
                        "offset_end": 54,
                    },
                ],
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.status_change_review_items >= 1
    postgres_session.expire_all()
    refreshed_project = postgres_session.get(Project, project.id)
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_project is not None
    assert refreshed_reference is not None
    assert refreshed_project.total_units == 140
    assert refreshed_project.workforce_units == 16
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.project_id == project.id
    assert evidence.source_type == "news_article"
    assert evidence.extracted_fields["canonical_address"]["value"] == canonical_address
    assert evidence.extracted_fields["total_units"]["value"] == 140
    assert evidence.extracted_fields["total_units"]["confidence"] == "high"
    assert evidence.extracted_fields["workforce_units"]["value"] == 16
    assert evidence.extracted_fields["workforce_units"]["highlights"][0]["field"] == (
        "workforce_units"
    )
    assert evidence.extracted_fields["total_units"]["highlights"][0]["passage"].startswith(
        "Atlas broke ground"
    )
    total_units_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
            ReviewItem.field_name == "total_units",
        )
    ).scalar_one()
    assert total_units_item.source_run_id == source_run.id
    assert total_units_item.winning_evidence_id == evidence.id
    assert total_units_item.payload["changes"] == [
        {
            "field": "total_units",
            "field_name": "total_units",
            "old_value": 100,
            "new_value": 140,
            "priority": "medium",
            "source": "news_article",
            "evidence_id": str(evidence.id),
        }
    ]
    all_status_items = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
        )
    ).scalars()
    assert all(len(item.payload.get("changes") or []) <= 1 for item in all_status_items)


def test_news_integration_semantic_strong_status_auto_promotes(
    postgres_session: Session,
) -> None:
    _ensure_semantic_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("1234 Semantic Boulevard, Los Angeles, CA 90026")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Semantic Tower",
        total_units=100,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Semantic Tower",
                candidate_address="1234 Semantic Boulevard, Los Angeles, CA 90026",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    semantic_client = FakeSemanticClient(
        _semantic_payload(
            reference_id=reference.id,
            reason_code="news_topped_out",
            canonical_value="topped_out",
            confidence="high",
        )
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        semantic_client=semantic_client,
        settings=Settings(
            _env_file=None,
            news_use_legacy_semantic=False,
            news_use_legacy_pass3=False,
            agent_enabled_for_news=False,
        ),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.semantic_result is not None
    assert result.semantic_result.parse_status == NewsExtractionParseStatus.OK.value
    assert len(semantic_client.prompts) == 1
    prompt_payload = _json_payload(semantic_client.prompts[0].user_text)
    assert prompt_payload["project_context"][0]["project_id"] == str(project.id)
    postgres_session.expire_all()
    refreshed_project = postgres_session.get(Project, project.id)
    assert refreshed_project is not None
    assert refreshed_project.pipeline_status == PipelineStatus.UNDER_CONSTRUCTION
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.extracted_fields["pipeline_status"]["value"] == (
        PipelineStatus.UNDER_CONSTRUCTION.value
    )
    assert evidence.extracted_fields["pipeline_status"]["semantic"]["reason_code"] == (
        "news_topped_out"
    )
    assert evidence.extracted_fields["pipeline_status"]["semantic"][
        "promotes_status_alone"
    ] is True


def test_news_integration_semantic_move_ins_event_token_promotes_complete(
    postgres_session: Session,
) -> None:
    _ensure_semantic_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("4321 Move In Way, Los Angeles, CA 90026")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Move In Tower",
        total_units=100,
    )
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Move In Tower",
                candidate_address="4321 Move In Way, Los Angeles, CA 90026",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    semantic_client = FakeSemanticClient(
        _semantic_payload(
            reference_id=reference.id,
            reason_code="news_first_move_ins",
            canonical_value="first_move_ins",
            confidence="high",
        )
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        semantic_client=semantic_client,
        settings=Settings(
            _env_file=None,
            news_use_legacy_semantic=False,
            news_use_legacy_pass3=False,
            agent_enabled_for_news=False,
        ),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.semantic_result is not None
    assert result.semantic_result.parse_status == NewsExtractionParseStatus.OK.value
    postgres_session.expire_all()
    refreshed_project = postgres_session.get(Project, project.id)
    assert refreshed_project is not None
    assert refreshed_project.pipeline_status == PipelineStatus.COMPLETE
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.extracted_fields["pipeline_status"]["value"] == (
        PipelineStatus.COMPLETE.value
    )


def test_news_integration_semantic_uncorroborated_status_creates_review_item(
    postgres_session: Session,
) -> None:
    _ensure_semantic_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("9876 Groundbreak Avenue, Los Angeles, CA 90026")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Groundbreak Tower",
        total_units=100,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Groundbreak Tower",
                candidate_address="9876 Groundbreak Avenue, Los Angeles, CA 90026",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    semantic_client = FakeSemanticClient(
        _semantic_payload(
            reference_id=reference.id,
            reason_code="news_status_uncorroborated_high_quality_permit_jurisdiction",
            canonical_value=PipelineStatus.UNDER_CONSTRUCTION.value,
            confidence="medium",
            requires_corroboration=True,
        )
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        semantic_client=semantic_client,
        settings=Settings(
            _env_file=None,
            news_use_legacy_semantic=False,
            news_use_legacy_pass3=False,
            agent_enabled_for_news=False,
        ),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.review_items_created >= 1
    assert result.semantic_result is not None
    postgres_session.expire_all()
    refreshed_project = postgres_session.get(Project, project.id)
    assert refreshed_project is not None
    assert refreshed_project.pipeline_status == PipelineStatus.PROPOSED
    item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.NEWS_STATUS_UNCORROBORATED,
        )
    ).scalar_one()
    assert item.priority == Priority.MEDIUM
    assert item.payload["reason_code"] == (
        "news_status_uncorroborated_high_quality_permit_jurisdiction"
    )
    assert item.payload["proposed_value"] == PipelineStatus.UNDER_CONSTRUCTION.value
    assert item.payload["semantic_interpretation_id"] == str(
        result.semantic_result.semantic_interpretation_id
    )
    assert item.payload["system_recommendation"]["action"] == (
        "keep_current_until_corroborated_or_researcher_confirms"
    )


def test_news_integration_semantic_prompt_uses_matched_project_jurisdiction(
    postgres_session: Session,
) -> None:
    _ensure_semantic_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    source.market = None
    source.jurisdiction = None
    source.market_id = None
    source.jurisdiction_id = None
    la_jurisdiction = _jurisdiction(postgres_session, "city_of_los_angeles")
    canonical_address = _canonical("4321 Policy Way, Los Angeles, CA 90026")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Policy Tower",
        developer="Atlas Development",
        total_units=100,
    )
    project.market_ref = la_jurisdiction.market
    project.market_id = la_jurisdiction.market_id
    project.jurisdiction_ref = la_jurisdiction
    project.jurisdiction_id = la_jurisdiction.id
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Policy Tower",
                candidate_address="4321 Policy Way, Los Angeles, CA 90026",
                candidate_developer="Atlas Development",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    semantic_client = FakeSemanticClient(
        _semantic_payload(
            reference_id=reference.id,
            reason_code="news_status_uncorroborated_high_quality_permit_jurisdiction",
            canonical_value=PipelineStatus.UNDER_CONSTRUCTION.value,
            confidence="medium",
            requires_corroboration=True,
        )
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        semantic_client=semantic_client,
        settings=Settings(
            _env_file=None,
            news_use_legacy_semantic=False,
            news_use_legacy_pass3=False,
            agent_enabled_for_news=False,
        ),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.semantic_result is not None
    prompt_payload = _json_payload(semantic_client.prompts[0].user_text)
    assert prompt_payload["fallback_jurisdiction_policy"]["policy_source"] == "default"
    assert prompt_payload["fallback_jurisdiction_policy"]["policy_scope"] == (
        "article_source_fallback"
    )
    assert prompt_payload["fallback_jurisdiction_policy"]["permit_data_quality"] == "low"
    assert prompt_payload["project_context"][0]["project_id"] == str(project.id)
    assert prompt_payload["project_context"][0]["jurisdiction_slug"] == (
        "city_of_los_angeles"
    )
    assert prompt_payload["project_context"][0]["jurisdiction_policy"][
        "permit_data_quality"
    ] == "high"
    assert prompt_payload["project_context"][0]["jurisdiction_policy"]["policy_scope"] == (
        "matched_project"
    )
    assert prompt_payload["project_context"][0]["jurisdiction_policy"][
        "news_status_promotion_policy"
    ] == "wait_for_permit_corroboration"
    postgres_session.expire_all()
    refreshed_project = postgres_session.get(Project, project.id)
    assert refreshed_project is not None
    assert refreshed_project.pipeline_status == PipelineStatus.PROPOSED
    item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.NEWS_STATUS_UNCORROBORATED,
        )
    ).scalar_one()
    assert item.payload["semantic_interpretation_id"] == str(
        result.semantic_result.semantic_interpretation_id
    )


def test_news_integration_semantic_prompt_uses_possible_candidate_jurisdiction(
    postgres_session: Session,
) -> None:
    _ensure_semantic_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    source.market = None
    source.jurisdiction = None
    source.market_id = None
    source.jurisdiction_id = None
    la_jurisdiction = _jurisdiction(postgres_session, "city_of_los_angeles")
    project = _project(
        source,
        canonical_address=_canonical("2468 Candidate Way, Los Angeles, CA 90026"),
        project_name="Existing Policy Candidate",
        developer="Candidate Developer",
        total_units=100,
    )
    project.market_ref = la_jurisdiction.market
    project.market_id = la_jurisdiction.market_id
    project.jurisdiction_ref = la_jurisdiction
    project.jurisdiction_id = la_jurisdiction.id
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Different Policy Proposal",
                candidate_address="2468 Candidate Way, Los Angeles",
                candidate_developer="Candidate Developer",
                candidate_unit_total=100,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    semantic_client = FakeSemanticClient({"interpretations": [], "diagnostic": {}})

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        semantic_client=semantic_client,
        settings=Settings(
            _env_file=None,
            news_use_legacy_semantic=False,
            news_use_legacy_pass3=False,
            agent_enabled_for_news=False,
        ),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.possible == 1
    assert result.semantic_result is not None
    prompt_payload = _json_payload(semantic_client.prompts[0].user_text)
    assert prompt_payload["fallback_jurisdiction_policy"]["permit_data_quality"] == "low"
    assert prompt_payload["project_context"][0]["context_role"] == "candidate_project"
    assert prompt_payload["project_context"][0]["project_id"] == str(project.id)
    assert prompt_payload["project_context"][0]["match_status"] == NewsMatchStatus.POSSIBLE.value
    assert prompt_payload["project_context"][0]["jurisdiction_slug"] == (
        "city_of_los_angeles"
    )
    assert prompt_payload["project_context"][0]["jurisdiction_policy"][
        "permit_data_quality"
    ] == "high"
    assert prompt_payload["project_context"][0]["jurisdiction_policy"]["policy_scope"] == (
        "candidate_project"
    )


def test_news_field_context_omits_news_winner_when_non_news_evidence_wins() -> None:
    news_evidence = Evidence(
        id=uuid.uuid4(),
        source_type="news_article",
        source_tier=2,
        ingest_method="news_paste_a_link",
        collected_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        evidence_date=datetime(2026, 4, 29, tzinfo=UTC).date(),
        extracted_fields={"total_units": {"value": 140, "confidence": "high"}},
    )
    costar_evidence_id = uuid.uuid4()
    context = _ProjectIntegrationContext(
        references=[
            _ConfirmedReference(
                reference=NewsProjectReference(id=uuid.uuid4()),
                match=None,
                evidence=news_evidence,
            )
        ]
    )
    resolution_result = SimpleNamespace(
        field_resolutions={
            "total_units": FieldResolution(
                field_name="total_units",
                value=160,
                confidence=StatusConfidence.MEDIUM,
                evidence_ids=[costar_evidence_id],
                rule_applied="most_recent_wins",
            )
        }
    )

    field_context = _field_reference_context(
        field_name="total_units",
        context=context,
        resolution_result=resolution_result,
    )

    assert field_context["reference"] is None
    assert field_context["winning_evidence_id"] is None
    assert field_context["resolution_winning_evidence_id"] == costar_evidence_id


def test_news_integration_runs_pass3b_and_integrates_latest_extraction(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("777 Recheck Avenue, Los Angeles, CA 90012"),
        project_name="Recheck Tower",
        developer="Atlas Development",
        total_units=80,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    initial_extraction, initial_reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Unmatched New Tower",
                candidate_developer="New Sponsor",
                candidate_unit_total=60,
            )
        ],
    )
    article.current_extraction_id = initial_extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    reextraction_ids: list[uuid.UUID] = []

    def fake_reextract(article_id: uuid.UUID, **kwargs) -> NewsExtractionRunResult:
        with task_session_factory() as session:
            reloaded_article = session.get(NewsArticle, article_id)
            assert reloaded_article is not None
            extraction, _reference = _add_extraction(
                session,
                article=reloaded_article,
                pass_name=NewsExtractionPass.REEXTRACTION.value,
                triggered_by=str(kwargs["triggered_by"]),
                supersedes_extraction_id=kwargs["prior_extraction_id"],
                references=[
                    _reference_payload(
                        candidate_name="Recheck Tower",
                        candidate_address="777 Recheck Avenue, Los Angeles, CA 90012",
                        candidate_developer="Atlas Development",
                        candidate_unit_total=120,
                    )
                ],
            )
            reloaded_article.current_extraction_id = extraction.id
            reloaded_article.current_extraction_version += 1
            session.commit()
            reextraction_ids.append(extraction.id)
            return NewsExtractionRunResult(
                article_id=article_id,
                extraction_id=extraction.id,
                relevance="confirmed",
                reference_count=1,
                parse_status=NewsExtractionParseStatus.OK.value,
                triggered_by=str(kwargs["triggered_by"]),
            )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=task_session_factory,
        reextraction_runner=fake_reextract,
        settings=Settings(news_use_legacy_pass3=True),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.pass3b_triggered is True
    assert result.confirmed == 1
    assert result.new_candidate == 0
    assert result.extraction_id == reextraction_ids[0]
    postgres_session.expire_all()
    refreshed_article = postgres_session.get(NewsArticle, article.id)
    refreshed_initial = postgres_session.get(NewsProjectReference, initial_reference.id)
    assert refreshed_article is not None
    assert refreshed_initial is not None
    assert refreshed_article.current_extraction_id == reextraction_ids[0]
    assert refreshed_initial.match_status == NewsMatchStatus.SUPERSEDED_BY_REEXTRACTION.value
    new_candidate_items = (
        postgres_session.execute(
            select(ReviewItem).where(
                ReviewItem.source_run_id == source_run.id,
                ReviewItem.item_type == ReviewItemType.NEW_CANDIDATE,
            )
        )
        .scalars()
        .all()
    )
    assert new_candidate_items == []
    evidence = postgres_session.execute(
        select(Evidence).where(
            Evidence.project_id == project.id,
            Evidence.source_type == "news_article",
        )
    ).scalar_one()
    assert evidence.project_id == project.id


def test_news_agent_trigger_preflight_preserves_legacy_pass3b_new_candidate(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Legacy Pass 3b Tower",
                candidate_address="500 Legacy Pass 3b Avenue, Los Angeles, CA 90012",
                candidate_unit_total=120,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    postgres_session.flush()
    task_session_factory = sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )

    assert (
        news_article_agent_trigger_reasons(
            article.id,
            session_factory=task_session_factory,
            settings=Settings(
                agent_enabled_for_news=True,
                news_use_legacy_pass3=True,
            ),
            now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        )
        == ()
    )
    assert news_article_agent_trigger_reasons(
        article.id,
        session_factory=task_session_factory,
        settings=Settings(
            agent_enabled_for_news=True,
            news_use_legacy_pass3=False,
        ),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    ) == ("new_candidate",)


def test_news_integration_supersedes_stale_article_evidence_after_reextraction(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    first_project = _project(
        source,
        canonical_address=_canonical("1100 First Street, Los Angeles, CA 90012"),
        project_name="First News Tower",
        developer="Atlas Development",
        total_units=50,
    )
    second_project = _project(
        source,
        canonical_address=_canonical("1200 Second Street, Los Angeles, CA 90012"),
        project_name="Second News Tower",
        developer="Atlas Development",
        total_units=60,
    )
    article = _article(source)
    postgres_session.add_all([first_project, second_project, article])
    postgres_session.flush()
    initial_extraction, _first_reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="First News Tower",
                candidate_address="1100 First Street, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=50,
            ),
            _reference_payload(
                candidate_name="Second News Tower",
                candidate_address="1200 Second Street, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=60,
            ),
        ],
    )
    article.current_extraction_id = initial_extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    task_session_factory = _task_session_factory(postgres_session)

    initial_result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert initial_result.confirmed == 2
    initial_evidence = _article_evidence_rows(postgres_session, article.id)
    assert len(initial_evidence) == 2
    assert all(row.superseded_at is None for row in initial_evidence)

    reextraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        pass_name=NewsExtractionPass.REEXTRACTION.value,
        triggered_by="manual_recheck",
        supersedes_extraction_id=initial_extraction.id,
        references=[
            _reference_payload(
                candidate_name="First News Tower",
                candidate_address="1100 First Street, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=55,
            )
        ],
    )
    article.current_extraction_id = reextraction.id
    article.current_extraction_version = 2
    postgres_session.flush()

    second_result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 30, 13, 0, tzinfo=UTC),
    )

    assert second_result.confirmed == 1
    postgres_session.expire_all()
    all_evidence = _article_evidence_rows(postgres_session, article.id)
    active_evidence = [row for row in all_evidence if row.superseded_at is None]
    stale_evidence = [row for row in all_evidence if row.superseded_at is not None]
    assert len(active_evidence) == 1
    assert len(stale_evidence) == 2
    assert active_evidence[0].raw_data["extraction_id"] == str(reextraction.id)
    stale_references = (
        postgres_session.execute(
            select(NewsProjectReference).where(
                NewsProjectReference.extraction_id == initial_extraction.id
            )
        )
        .scalars()
        .all()
    )
    assert {reference.match_status for reference in stale_references} == {
        NewsMatchStatus.SUPERSEDED_BY_REEXTRACTION.value
    }


def test_news_integration_marks_all_non_current_references_superseded(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("1300 Current Street, Los Angeles, CA 90012"),
        project_name="Current News Tower",
        developer="Atlas Development",
        total_units=75,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    initial_extraction, initial_reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Initial News Tower",
                candidate_address="1100 Initial Street, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=55,
            )
        ],
    )
    pass3a_extraction, pass3a_reference = _add_extraction(
        postgres_session,
        article=article,
        pass_name=NewsExtractionPass.REEXTRACTION.value,
        triggered_by="pass1_pass2_conflict",
        supersedes_extraction_id=initial_extraction.id,
        references=[
            _reference_payload(
                candidate_name="Intermediate News Tower",
                candidate_address="1200 Intermediate Street, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=65,
            )
        ],
    )
    final_extraction, final_reference = _add_extraction(
        postgres_session,
        article=article,
        pass_name=NewsExtractionPass.REEXTRACTION.value,
        triggered_by="pass2_new_candidate",
        supersedes_extraction_id=pass3a_extraction.id,
        references=[
            _reference_payload(
                candidate_name="Current News Tower",
                candidate_address="1300 Current Street, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=75,
            )
        ],
    )
    article.current_extraction_id = final_extraction.id
    article.current_extraction_version = 3
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    task_session_factory = _task_session_factory(postgres_session)

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 30, 13, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    postgres_session.expire_all()
    initial_reference = postgres_session.get(NewsProjectReference, initial_reference.id)
    pass3a_reference = postgres_session.get(NewsProjectReference, pass3a_reference.id)
    final_reference = postgres_session.get(NewsProjectReference, final_reference.id)
    assert initial_reference is not None
    assert pass3a_reference is not None
    assert final_reference is not None
    assert initial_reference.match_status == NewsMatchStatus.SUPERSEDED_BY_REEXTRACTION.value
    assert pass3a_reference.match_status == NewsMatchStatus.SUPERSEDED_BY_REEXTRACTION.value
    assert final_reference.match_status == NewsMatchStatus.CONFIRMED.value


def test_force_project_id_is_honored_for_single_reference_and_reported_for_multi(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("1888 Forced Avenue, Los Angeles, CA 90012"),
        project_name="Forced Match Tower",
        developer="Atlas Development",
        total_units=44,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Loose Article Mention",
                candidate_developer="Different Sponsor",
                candidate_unit_total=22,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    task_session_factory = _task_session_factory(postgres_session)

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.force_project_id_dropped_reason is None
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.matched_project_id == project.id

    multi_article = _article(source)
    postgres_session.add(multi_article)
    postgres_session.flush()
    multi_extraction, _multi_reference = _add_extraction(
        postgres_session,
        article=multi_article,
        references=[
            _reference_payload(candidate_name="Mention Only One"),
            _reference_payload(candidate_name="Mention Only Two"),
        ],
    )
    multi_article.current_extraction_id = multi_extraction.id
    multi_article.current_extraction_version = 1
    postgres_session.flush()

    multi_result = run_news_integration_for_article(
        multi_article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=task_session_factory,
        now=datetime(2026, 4, 30, 12, 5, tzinfo=UTC),
    )

    assert multi_result.force_project_id_dropped_reason == "multi_reference"
    assert multi_result.progress_payload["force_project_id_dropped_reason"] == ("multi_reference")


def test_news_integration_routes_pass1_pass2_conflict_to_agent(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2100 Conflict Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Conflict Check Tower",
        total_units=250,
    )
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "310 apartments",
                "offset_start": 15,
                "offset_end": 29,
                "canonical": 310,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Conflict Check Tower",
                candidate_address="2100 Conflict Boulevard, Los Angeles, CA 90012",
                candidate_unit_total=250,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient({"decision": "no_change"})

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert client.requests
    request = client.requests[0]
    assert request.trigger_reasons == ("pass1_pass2_conflict",)
    assert request.intake.payload["pass1_pass2_conflicts"][0]["field"] == "total_units"
    assert request.intake.payload["pass1_pass2_conflicts"][0]["structural_value"] == 310
    assert request.intake.payload["pass1_pass2_conflicts"][0]["extracted_value"] == 250
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    review_items = (
        postgres_session.execute(
            select(ReviewItem).where(
                ReviewItem.source_run_id == source_run.id,
                ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
            )
        )
        .scalars()
        .all()
    )
    assert review_items == []
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["pass1_pass2_conflict"]


def test_news_integration_escalated_pass1_pass2_conflict_creates_review_item(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2110 Conflict Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Escalated Conflict Tower",
        total_units=250,
    )
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "310 apartments",
                "offset_start": 15,
                "offset_end": 29,
                "canonical": 310,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Escalated Conflict Tower",
                candidate_address="2110 Conflict Boulevard, Los Angeles, CA 90012",
                candidate_unit_total=250,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "escalated",
            "reason": "Structural count and extracted count disagree.",
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.status_change_review_items == 1
    postgres_session.expire_all()
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
            ReviewItem.field_name == "total_units",
        )
    ).scalar_one()
    assert review_item.priority == Priority.MEDIUM
    assert review_item.winning_evidence_id is not None
    assert review_item.payload["origin"] == "agent_pass1_pass2_conflict"
    assert review_item.payload["structural_value"] == 310
    assert review_item.payload["extracted_value"] == 250
    assert review_item.payload["pass1_pass2_conflicts"][0]["field"] == "total_units"
    assert review_item.payload["reasoning_trace"] == (
        "Agent reviewed deterministic matcher output against available tools."
    )
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.review_item_id == review_item.id
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert review_item.payload["agent_run_id"] == str(agent_run.id)
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_integration_routes_material_contradiction_to_agent(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2130 Conflict Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Material Conflict Tower",
        total_units=100,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Material Conflict Tower",
                candidate_address="2130 Conflict Boulevard, Los Angeles, CA 90012",
                candidate_unit_total=130,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient({"decision": "no_change"})

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert client.requests[0].trigger_reasons == ("material_contradiction",)
    contradiction = client.requests[0].intake.payload["material_contradictions"][0]
    assert contradiction["field"] == "total_units"
    assert contradiction["current_value"] == 100
    assert contradiction["candidate_value"] == 130
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["material_contradiction"]


def test_news_integration_material_contradiction_agent_can_downgrade_to_possible(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2140 Conflict Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Downgrade Conflict Tower",
        total_units=100,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Downgrade Conflict Tower",
                candidate_address="2140 Conflict Boulevard, Los Angeles, CA 90012",
                candidate_unit_total=140,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "downgrade_to_possible",
            "project_id": str(project.id),
            "confidence": 0.62,
            "reason": "Unit delta makes the deterministic attribution suspect.",
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 0
    assert result.possible == 1
    assert result.status_change_review_items == 0
    assert client.requests[0].trigger_reasons == ("material_contradiction",)
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.POSSIBLE.value
    assert refreshed_reference.matched_project_id is None
    assert refreshed_reference.review_item_id is not None
    evidence = postgres_session.get(Evidence, refreshed_reference.matched_evidence_id)
    assert evidence is not None
    assert evidence.project_id is None
    review_item = postgres_session.get(ReviewItem, refreshed_reference.review_item_id)
    assert review_item is not None
    assert review_item.item_type == ReviewItemType.POSSIBLE_MATCH
    assert review_item.payload["match"]["diagnostics"]["agent_material_contradictions"][0][
        "field"
    ] == "total_units"
    assert review_item.payload["candidate_project_ids"] == [str(project.id)]
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["material_contradiction"]
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_integration_agent_creates_override_contradiction_item(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2150 Override Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Override Conflict Tower",
        total_units=100,
    )
    project.workforce_units = 10
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    upsert_researcher_overrides(
        postgres_session,
        project,
        {
            "workforce_units": {
                "value": 10,
                "set_by": "reviewer@example.com",
                "set_at": "2026-04-01T12:00:00+00:00",
                "mode": "review_protected",
                "baseline": {
                    "evidence_date": "2026-04-01",
                    "collected_at": "2026-04-01T12:00:00+00:00",
                    "source_tier": 3,
                    "source_type": "costar",
                },
            }
        },
    )
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Override Conflict Tower",
                candidate_address="2150 Override Boulevard, Los Angeles, CA 90012",
                candidate_unit_workforce=20,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "recommend_accept_new",
            "confidence": 0.81,
            "reason": "The article gives a newer explicit workforce-unit count.",
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert client.requests[0].trigger_reasons == ("override_contradiction",)
    contradiction = client.requests[0].intake.payload["override_contradictions"][0]
    assert contradiction["field"] == "workforce_units"
    assert contradiction["current_override"]["value"] == 10
    assert contradiction["candidate"]["value"] == 20
    postgres_session.expire_all()
    refreshed_project = postgres_session.get(Project, project.id)
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_project is not None
    assert refreshed_project.workforce_units == 10
    assert refreshed_reference is not None
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
            ReviewItem.state.in_(["open", "staged"]),
        )
    ).scalar_one()
    alternatives = review_item.payload["proposed_alternatives"]
    assert [alternative["value"] for alternative in alternatives] == [20, 10]
    assert review_item.payload["agent_origin"] == "agent_override_contradiction"
    assert review_item.payload["system_recommendation"]["action"] == (
        "researcher_accept_new_recommended"
    )
    assert refreshed_reference.review_item_id == review_item.id
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["override_contradiction"]
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_integration_override_contradiction_skips_raw_status_signal(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2160 Override Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Override Status Tower",
    )
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    upsert_researcher_overrides(
        postgres_session,
        project,
        {
            "pipeline_status": {
                "value": PipelineStatus.UNDER_CONSTRUCTION.value,
                "set_by": "reviewer@example.com",
                "set_at": "2026-04-01T12:00:00+00:00",
                "mode": "review_protected",
                "baseline": {
                    "evidence_date": "2026-04-01",
                    "collected_at": "2026-04-01T12:00:00+00:00",
                    "source_tier": 3,
                    "source_type": "costar",
                },
            }
        },
    )
    extraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Override Status Tower",
                candidate_address="2160 Override Boulevard, Los Angeles, CA 90012",
                candidate_status_signal="topped_out",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "recommend_accept_new",
            "confidence": 0.81,
            "reason": "Unused because raw status signals should not trigger override review.",
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        force_project_id=project.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert client.requests == []
    assert postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one_or_none() is None
    assert postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one_or_none() is None


def test_news_integration_creates_possible_match_review_item(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2200 Possible Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Possible Match Tower",
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Possible Match Tower",
                candidate_address="2200 Possible Boulevard, Los Angeles, CA 90012",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient({"decision": "no_change"})

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.possible == 1
    assert client.requests
    request = client.requests[0]
    assert request.trigger_reasons == ("possible_multi_candidate",)
    assert request.matcher_results[0]["status"] == NewsMatchStatus.POSSIBLE.value
    assert request.matcher_results[0]["candidate_project_ids"] == [str(project.id)]
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.POSSIBLE.value
    review_item = postgres_session.get(ReviewItem, refreshed_reference.review_item_id)
    assert review_item is not None
    assert review_item.item_type == ReviewItemType.POSSIBLE_MATCH
    assert review_item.payload["candidate_project_ids"] == [str(project.id)]
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["possible_multi_candidate"]
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_possible_match_agent_can_confirm_candidate_project(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2300 Candidate Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Candidate Pick Tower",
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Candidate Pick Tower",
                candidate_address="2300 Candidate Boulevard, Los Angeles, CA 90012",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "confirm_existing_project",
            "project_id": str(project.id),
            "confidence": 0.91,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.possible == 0
    assert client.requests[0].trigger_reasons == ("possible_multi_candidate",)
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    assert refreshed_reference.matched_project_id == project.id
    assert refreshed_reference.match_candidates["match_type"] == ("agent_confirmed_possible_match")
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.project_id == project.id
    possible_items = (
        postgres_session.execute(
            select(ReviewItem).where(
                ReviewItem.item_type == ReviewItemType.POSSIBLE_MATCH,
                ReviewItem.source_run_id == source_run.id,
            )
        )
        .scalars()
        .all()
    )
    assert possible_items == []


def test_news_possible_match_agent_rejects_off_list_project_id(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2400 Guarded Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Guarded Candidate Tower",
    )
    other_project = _project(
        source,
        canonical_address=_canonical("2500 Other Boulevard, Los Angeles, CA 90012"),
        project_name="Other Agent Tower",
    )
    article = _article(source)
    postgres_session.add_all([project, other_project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Guarded Candidate Tower",
                candidate_address="2400 Guarded Boulevard, Los Angeles, CA 90012",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "confirm_existing_project",
            "project_id": str(other_project.id),
            "confidence": 0.91,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 0
    assert result.possible == 1
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.POSSIBLE.value
    assert refreshed_reference.matched_project_id is None
    review_item = postgres_session.get(ReviewItem, refreshed_reference.review_item_id)
    assert review_item is not None
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.agent_revised_verdict == {
        "decision": "confirm_existing_project",
        "project_id": str(other_project.id),
        "confidence": 0.91,
    }
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_possible_match_requires_live_llm_opt_in_without_injected_client(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2600 Live Guard Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Possible Live Guard Tower",
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Possible Live Guard Tower",
                candidate_address="2600 Live Guard Boulevard, Los Angeles, CA 90012",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()

    with pytest.raises(RuntimeError, match="AGENT_ALLOW_LIVE_LLM=true"):
        run_news_integration_for_article(
            article.id,
            source_run_id=source_run.id,
            session_factory=_task_session_factory(postgres_session),
            settings=Settings(
                agent_enabled_for_news=True,
                agent_allow_live_llm=False,
                news_use_legacy_pass3=False,
            ),
            now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        )


def test_news_low_confidence_confirmed_agent_links_status_review_item(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2700 Low Confidence Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Low Confidence Tower",
        developer="Atlas Development",
        total_units=80,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Low Confidence Tower",
                candidate_address="2700 Low Confidence Boulevard, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_unit_total=88,
                candidate_confidence="low",
                passage_excerpts=[
                    {
                        "field": "candidate_unit_total",
                        "value": 88,
                        "passage": "Low Confidence Tower would include 88 apartments.",
                    }
                ],
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient({"decision": "no_change"})

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.status_change_review_items >= 1
    assert client.requests
    request = client.requests[0]
    assert request.trigger_reasons == ("low_confidence",)
    assert request.intake.payload["low_confidence_fields"] == [
        "total_units",
        "developer",
        "candidate_address",
    ]
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
            ReviewItem.field_name == "total_units",
        )
    ).scalar_one()
    assert review_item.priority == Priority.LOW.value
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["low_confidence"]
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_low_confidence_requires_live_llm_opt_in_without_injected_client(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2800 Low Guard Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Low Guard Tower",
        developer="Atlas Development",
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Low Guard Tower",
                candidate_address="2800 Low Guard Boulevard, Los Angeles, CA 90012",
                candidate_developer="Atlas Development",
                candidate_confidence="low",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()

    with pytest.raises(RuntimeError, match="AGENT_ALLOW_LIVE_LLM=true"):
        run_news_integration_for_article(
            article.id,
            source_run_id=source_run.id,
            session_factory=_task_session_factory(postgres_session),
            settings=Settings(
                agent_enabled_for_news=True,
                agent_allow_live_llm=False,
                news_use_legacy_pass3=False,
            ),
            now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        )


def test_news_low_confidence_agent_can_promote_discarded_reference(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("2900 Agent Recovery Boulevard, Los Angeles, CA 90012"),
        project_name="Agent Recovery Tower",
        developer="Recovery Homes",
        total_units=64,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Low Confidence Standalone",
                candidate_developer="Standalone Homes",
                candidate_unit_total=64,
                candidate_confidence="low",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "promote_existing_project",
            "project_id": str(project.id),
            "confidence": 0.9,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.discarded == 0
    assert client.requests[0].trigger_reasons == ("low_confidence",)
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    assert refreshed_reference.matched_project_id == project.id
    assert refreshed_reference.match_candidates["match_type"] == ("agent_promoted_existing_project")
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.project_id == project.id
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == ["low_confidence"]


def test_news_combined_possible_low_confidence_uses_confirm_verdict(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    canonical_address = _canonical("2920 Combined Possible Boulevard, Los Angeles, CA 90012")
    project = _project(
        source,
        canonical_address=canonical_address,
        project_name="Combined Possible Tower",
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Combined Possible Tower",
                candidate_address="2920 Combined Possible Boulevard, Los Angeles, CA 90012",
                candidate_unit_total=120,
                candidate_confidence="low",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "confirm_existing_project",
            "project_id": str(project.id),
            "confidence": 0.9,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert client.requests[0].trigger_reasons == (
        "possible_multi_candidate",
        "low_confidence",
    )
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    assert refreshed_reference.matched_project_id == project.id
    assert refreshed_reference.match_candidates["match_type"] == ("agent_confirmed_possible_match")
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == [
        "possible_multi_candidate",
        "low_confidence",
    ]


def test_news_combined_new_candidate_low_confidence_uses_promote_verdict(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("2930 Combined New Boulevard, Los Angeles, CA 90012"),
        project_name="Combined New Tower",
    )
    article = _article(source)
    article.structural_signals = {
        "extractor_version": "v1",
        "ran_at": "2026-04-29T12:00:00+00:00",
        "signals": [
            {
                "extractor": "unit_count",
                "raw_match": "100 apartments",
                "offset_start": 15,
                "offset_end": 29,
                "canonical": 100,
                "confidence": 0.95,
                "metadata": {"label": "unit"},
            }
        ],
    }
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Low Confidence New Candidate",
                candidate_address="2931 Combined New Boulevard, Los Angeles, CA 90012",
                candidate_developer="Combined Homes",
                candidate_unit_total=80,
                candidate_confidence="low",
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()

    def fake_match(*_args: object, **_kwargs: object) -> NewsMatchResult:
        return NewsMatchResult(
            status=NewsMatchStatus.NEW_CANDIDATE,
            match_type="test_new_candidate_low_confidence",
            confidence=0.4,
            reason="Synthetic combined-trigger matcher result.",
        )

    monkeypatch.setattr("tcg_pipeline.news.integration.match_news_reference", fake_match)
    client = FakeNewsAgentClient(
        {
            "decision": "promote_existing_project",
            "project_id": str(project.id),
            "confidence": 0.9,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 1
    assert result.new_candidate == 0
    assert client.requests[0].trigger_reasons == (
        "pass1_pass2_conflict",
        "new_candidate",
        "low_confidence",
    )
    assert client.requests[0].intake.payload["pass1_pass2_conflicts"][0]["field"] == (
        "total_units"
    )
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    assert refreshed_reference.matched_project_id == project.id
    assert refreshed_reference.match_candidates["match_type"] == ("agent_promoted_existing_project")
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.triggered_by == [
        "pass1_pass2_conflict",
        "new_candidate",
        "low_confidence",
    ]


def test_news_new_candidate_accept_links_orphan_evidence(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Standalone News Project",
                candidate_address="998 Standalone Avenue, Los Angeles, CA 90012",
                candidate_developer="Standalone Homes",
                candidate_unit_total=88,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    task_session_factory = _task_session_factory(postgres_session)

    def skipped_reextract(article_id: uuid.UUID, **_kwargs) -> NewsExtractionRunResult:
        return NewsExtractionRunResult(
            article_id=article_id,
            extraction_id=None,
            relevance=None,
            reference_count=0,
            parse_status=None,
            skipped_reason="test_skip",
        )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=task_session_factory,
        reextraction_runner=skipped_reextract,
        settings=Settings(news_use_legacy_pass3=True),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.pass3b_triggered is True
    assert result.new_candidate == 1
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.NEW_CANDIDATE.value
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.project_id is None
    review_item = postgres_session.get(ReviewItem, refreshed_reference.review_item_id)
    assert review_item is not None
    assert review_item.source_run_id == source_run.id
    assert review_item.item_type == ReviewItemType.NEW_CANDIDATE
    assert review_item.payload["source_record_id"] == str(reference.id)
    assert (
        review_item.payload["mapped_fields"]["canonical_address"]
        == review_item.payload["canonical_address"]
    )

    accept_result = accept_review_item(
        postgres_session,
        review_item_id=review_item.id,
        actor="reviewer@example.com",
        create_new=True,
    )
    postgres_session.flush()
    postgres_session.refresh(evidence)
    postgres_session.refresh(review_item)

    assert accept_result.linked_evidence_count == 1
    assert accept_result.project_id is not None
    assert evidence.project_id == accept_result.project_id
    assert review_item.project_id == accept_result.project_id


def test_news_new_candidate_agent_no_change_creates_and_links_review_item(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Agent No Change Tower",
                candidate_address="221 Agent Lane, Los Angeles, CA 90012",
                candidate_developer="Agent Homes",
                candidate_unit_total=77,
                passage_excerpts=[
                    {
                        "field": "candidate_name",
                        "value": "Agent No Change Tower",
                        "passage": "Agent No Change Tower was proposed.",
                    }
                ],
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient({"decision": "no_change"})

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.pass3b_triggered is False
    assert result.new_candidate == 1
    assert client.requests
    request = client.requests[0]
    assert "body_text" not in request.intake.payload["article"]
    assert request.intake.payload["reference"]["candidate_name"] == "Agent No Change Tower"
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    review_item = postgres_session.get(ReviewItem, refreshed_reference.review_item_id)
    assert review_item is not None
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    assert agent_run.outcome == AgentRunOutcome.COMPLETED.value
    assert agent_run.agent_revised_verdict == {"decision": "no_change"}
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def test_news_new_candidate_requires_live_llm_opt_in_without_injected_client(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction, _reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Live Guard Tower",
                candidate_address="661 Guard Avenue, Los Angeles, CA 90012",
                candidate_developer="Guard Homes",
                candidate_unit_total=66,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()

    with pytest.raises(RuntimeError, match="AGENT_ALLOW_LIVE_LLM=true"):
        run_news_integration_for_article(
            article.id,
            source_run_id=source_run.id,
            session_factory=_task_session_factory(postgres_session),
            settings=Settings(
                agent_enabled_for_news=True,
                agent_allow_live_llm=False,
                news_use_legacy_pass3=False,
            ),
            now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        )


def test_news_new_candidate_agent_can_promote_existing_project(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("770 Existing Avenue, Los Angeles, CA 90012"),
        project_name="Existing Agent Tower",
        developer="Existing Sponsor",
        total_units=88,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Nearby Agent Proposal",
                candidate_developer="Different Sponsor",
                candidate_unit_total=88,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "promote_existing_project",
            "project_id": str(project.id),
            "confidence": 0.94,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.pass3b_triggered is False
    assert result.confirmed == 1
    assert result.new_candidate == 0
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.CONFIRMED.value
    assert refreshed_reference.matched_project_id == project.id
    assert refreshed_reference.match_candidates["match_type"] == ("agent_promoted_existing_project")
    evidence = postgres_session.execute(
        select(Evidence).where(Evidence.source_record_id == str(reference.id))
    ).scalar_one()
    assert evidence.project_id == project.id
    new_candidate_items = (
        postgres_session.execute(
            select(ReviewItem).where(
                ReviewItem.item_type == ReviewItemType.NEW_CANDIDATE,
                ReviewItem.source_run_id == source_run.id,
            )
        )
        .scalars()
        .all()
    )
    assert new_candidate_items == []


def test_news_new_candidate_agent_invalid_confidence_falls_back_to_review(
    postgres_session: Session,
) -> None:
    _ensure_news_integration_tables(postgres_session)
    _ensure_agent2_tables(postgres_session)
    source = _news_source(postgres_session, "news_paste_a_link")
    project = _project(
        source,
        canonical_address=_canonical("880 Invalid Confidence Avenue, Los Angeles, CA 90012"),
        project_name="Established Westside Homes",
        developer="Existing Sponsor",
        total_units=88,
    )
    article = _article(source)
    postgres_session.add_all([project, article])
    postgres_session.flush()
    extraction, reference = _add_extraction(
        postgres_session,
        article=article,
        references=[
            _reference_payload(
                candidate_name="Invalid Confidence Proposal",
                candidate_developer="Different Sponsor",
                candidate_unit_total=88,
            )
        ],
    )
    article.current_extraction_id = extraction.id
    article.current_extraction_version = 1
    source_run = _source_run(source)
    postgres_session.add(source_run)
    postgres_session.flush()
    client = FakeNewsAgentClient(
        {
            "decision": "promote_existing_project",
            "project_id": str(project.id),
            "confidence": 1.5,
        }
    )

    result = run_news_integration_for_article(
        article.id,
        source_run_id=source_run.id,
        session_factory=_task_session_factory(postgres_session),
        agent_client=client,
        settings=Settings(agent_enabled_for_news=True, news_use_legacy_pass3=False),
        now=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    assert result.confirmed == 0
    assert result.new_candidate == 1
    postgres_session.expire_all()
    refreshed_reference = postgres_session.get(NewsProjectReference, reference.id)
    assert refreshed_reference is not None
    assert refreshed_reference.match_status == NewsMatchStatus.NEW_CANDIDATE.value
    assert refreshed_reference.matched_project_id is None
    review_item = postgres_session.get(ReviewItem, refreshed_reference.review_item_id)
    assert review_item is not None
    agent_run = postgres_session.execute(
        select(AgentRun).where(AgentRun.intake_extraction_id == extraction.id)
    ).scalar_one()
    link = postgres_session.get(
        AgentRunReviewItem,
        (agent_run.id, review_item.id),
    )
    assert link is not None


def _ensure_news_integration_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "evidence",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_sources",
        "projects",
        "review_items",
        "source_runs",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running integration tests: {missing}")
    if "matched_evidence_id" not in {
        column["name"] for column in inspector.get_columns("news_project_references")
    }:
        pytest.skip("Apply latest Phase D news reference migrations before running tests.")


def _ensure_agent2_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "agent_runs",
        "agent_run_review_items",
        "cost_caps",
        "llm_cost_usage",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply AGENT.2 migrations before running agent integration tests: {missing}")


def _ensure_semantic_news_integration_tables(postgres_session: Session) -> None:
    _ensure_news_integration_tables(postgres_session)
    inspector = inspect(postgres_session.bind)
    required_tables = {"news_semantic_interpretations", "llm_cost_usage"}
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply semantic Pass 2c migrations before running tests: {missing}")


def _news_source(postgres_session: Session, slug: str) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == slug)
    ).scalar_one_or_none()
    if source is None:
        pytest.skip(f"Apply Phase D source seed migration before running tests: {slug}.")
    return source


def _jurisdiction(postgres_session: Session, slug: str) -> Jurisdiction:
    jurisdiction = postgres_session.execute(
        select(Jurisdiction).where(Jurisdiction.slug == slug)
    ).scalar_one_or_none()
    if jurisdiction is not None:
        return jurisdiction
    market = postgres_session.execute(
        select(Market).where(Market.slug == "los_angeles")
    ).scalar_one_or_none()
    if market is None:
        market = Market(slug="los_angeles", name="Los Angeles", state="CA")
        postgres_session.add(market)
        postgres_session.flush()
    jurisdiction = Jurisdiction(
        slug=slug,
        name="City of Los Angeles",
        state="CA",
        market=market,
    )
    postgres_session.add(jurisdiction)
    postgres_session.flush()
    return jurisdiction


def _canonical(raw_address: str) -> str:
    canonical = normalize_address(
        raw_address,
        city="Los Angeles",
        state="CA",
        market="los_angeles",
    ).canonical_address
    assert canonical is not None
    return canonical


def _project(
    source: NewsSource,
    *,
    canonical_address: str,
    project_name: str,
    developer: str | None = None,
    total_units: int | None = None,
) -> Project:
    market_slug = source.market.slug if source.market is not None else "los_angeles"
    market_id = source.market_id
    return Project(
        canonical_address=canonical_address,
        raw_addresses=[canonical_address],
        market=market_slug if market_slug != "unscoped" else "los_angeles",
        market_id=market_id if market_slug != "unscoped" else None,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=project_name,
        developer=developer,
        total_units=total_units,
        pipeline_status=PipelineStatus.PROPOSED,
    )


def _article(source: NewsSource) -> NewsArticle:
    return NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/news-d4-{uuid.uuid4().hex}",
        url_original="https://example.com/news-d4",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        body_text=(
            "Atlas Development announced a residential project in Los Angeles with "
            "updated unit counts and delivery timing."
        ),
        body_text_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        title="Atlas announces project",
        byline_author="Ava Reporter",
        published_at=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 4, 29, 12, 1, tzinfo=UTC),
        ingest_method="news_paste_a_link",
    )


def _source_run(source: NewsSource) -> SourceRun:
    return SourceRun(
        market=source.market.slug if source.market is not None else "unscoped",
        jurisdiction_id=source.jurisdiction_id,
        source_name=source.slug,
        collection_mode="single",
        trigger_type="user_initiated",
        records_pulled=1,
        rows_updated=1,
    )


def _task_session_factory(postgres_session: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def _article_evidence_rows(session: Session, article_id: uuid.UUID) -> list[Evidence]:
    rows = (
        session.execute(select(Evidence).where(Evidence.source_type == "news_article"))
        .scalars()
        .all()
    )
    return [
        row
        for row in rows
        if isinstance(row.raw_data, dict) and row.raw_data.get("article_id") == str(article_id)
    ]


def _add_extraction(
    session: Session,
    *,
    article: NewsArticle,
    references: list[dict],
    pass_name: str = NewsExtractionPass.EXTRACTION.value,
    triggered_by: str = "initial",
    supersedes_extraction_id: uuid.UUID | None = None,
) -> tuple[NewsExtraction, NewsProjectReference]:
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=pass_name,
        triggered_by=triggered_by,
        supersedes_extraction_id=supersedes_extraction_id,
        prompt_id=(
            "extract_v1" if pass_name == NewsExtractionPass.EXTRACTION.value else "reextract_v1"
        ),
        prompt_version="v1",
        prompt_hash=uuid.uuid4().hex,
        model="claude-opus-4-7",
        output_json={
            "relevance": "confirmed",
            "rejected_reason": None,
            "project_references": references,
            "diagnostic": {},
        },
        parse_status=NewsExtractionParseStatus.OK.value,
    )
    session.add(extraction)
    session.flush()
    rows = [
        _reference_from_payload(
            article=article,
            extraction=extraction,
            index=index,
            payload=payload,
        )
        for index, payload in enumerate(references)
    ]
    session.add_all(rows)
    session.flush()
    return extraction, rows[0]


def _reference_from_payload(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    index: int,
    payload: dict,
) -> NewsProjectReference:
    return NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=index,
        candidate_name=payload.get("candidate_name"),
        candidate_address=payload.get("candidate_address"),
        candidate_developer=payload.get("candidate_developer"),
        candidate_unit_total=payload.get("candidate_unit_total"),
        candidate_unit_affordable=payload.get("candidate_unit_affordable"),
        candidate_unit_market_rate=payload.get("candidate_unit_market_rate"),
        candidate_unit_workforce=payload.get("candidate_unit_workforce"),
        candidate_product_type=payload.get("candidate_product_type"),
        candidate_age_restriction=payload.get("candidate_age_restriction"),
        candidate_status_signal=payload.get("candidate_status_signal"),
        candidate_delivery_year_normalized=payload.get("candidate_delivery_year_normalized"),
        candidate_signal_flags=payload.get("candidate_signal_flags") or {},
        candidate_identifiers=payload.get("candidate_identifiers")
        or {"case_number": [], "permit_number": [], "apn": []},
        candidate_neighborhood=payload.get("candidate_neighborhood"),
        candidate_lat=payload.get("candidate_lat"),
        candidate_lng=payload.get("candidate_lng"),
        candidate_confidence=payload.get("candidate_confidence") or "high",
        passage_excerpts=payload.get("passage_excerpts") or [],
        match_status=NewsMatchStatus.PENDING.value,
    )


def _reference_payload(
    *,
    candidate_name: str | None = "Helio",
    candidate_address: str | None = None,
    candidate_developer: str | None = None,
    candidate_unit_total: int | None = None,
    candidate_unit_workforce: int | None = None,
    candidate_identifiers: dict[str, list[str]] | None = None,
    candidate_product_type: str | None = None,
    candidate_age_restriction: str | None = None,
    candidate_status_signal: str | None = None,
    candidate_confidence: str = "high",
    passage_excerpts: list[dict] | None = None,
    registry_project_id: str | None = None,
) -> dict:
    return {
        "candidate_name": candidate_name,
        "candidate_address": candidate_address,
        "candidate_developer": candidate_developer,
        "candidate_unit_total": candidate_unit_total,
        "candidate_unit_affordable": None,
        "candidate_unit_market_rate": None,
        "candidate_unit_workforce": candidate_unit_workforce,
        "candidate_product_type": candidate_product_type,
        "candidate_age_restriction": candidate_age_restriction,
        "candidate_status_signal": candidate_status_signal,
        "candidate_delivery_year_text": None,
        "candidate_delivery_year_normalized": None,
        "candidate_signal_flags": {},
        "candidate_identifiers": candidate_identifiers
        or {"case_number": [], "permit_number": [], "apn": []},
        "candidate_neighborhood": None,
        "candidate_lat": None,
        "candidate_lng": None,
        "candidate_confidence": candidate_confidence,
        "passage_excerpts": passage_excerpts or [],
        "registry_developer_id": None,
        "registry_project_id": registry_project_id,
    }


def _semantic_payload(
    *,
    reference_id: uuid.UUID,
    reason_code: str,
    canonical_value: str | None,
    confidence: str,
    requires_corroboration: bool = False,
) -> dict:
    return {
        "interpretations": [
            {
                "field_name": "pipeline_status",
                "canonical_value": canonical_value,
                "confidence": confidence,
                "reason_code": reason_code,
                "signal_flags": {},
                "source_anchors": [
                    {
                        "text": "the tower has topped out",
                        "offset_start": 10,
                        "offset_end": 34,
                        "field_name": "pipeline_status",
                        "metadata": {},
                    }
                ],
                "requires_corroboration": requires_corroboration,
                "metadata": {
                    "tense": "past_concurrent",
                    "reference_id": str(reference_id),
                },
            }
        ],
        "diagnostic": {"fixture": True},
    }


def _json_payload(value: str) -> dict:
    payload = json.loads(value)
    assert isinstance(payload, dict)
    return payload
