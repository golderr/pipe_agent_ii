from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.routers.activity import (
    MAX_INTERNAL_LIMIT,
    list_activity_events,
    list_activity_semantic_metrics,
)
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    ChangeLog,
    ChangeType,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsProjectReference,
    NewsSemanticInterpretation,
    NewsSource,
    PipelineStatus,
    Priority,
    Project,
    ResolutionLog,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    ScrapeJobKind,
    StatusConfidence,
)


def _auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="allowed@example.com",
        role="authenticated",
        claims={},
    )


def _project(
    postgres_session: Session,
    address: str,
    *,
    market: str = "los_angeles",
    jurisdiction: str = "city_of_los_angeles",
) -> Project:
    project = Project(
        canonical_address=address,
        raw_addresses=[address],
        market=market,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        jurisdiction=jurisdiction,
        pipeline_status=PipelineStatus.APPROVED,
        project_name=address,
    )
    postgres_session.add(project)
    postgres_session.flush()
    return project


def _agent_run(
    postgres_session: Session,
    project: Project | None,
    *,
    article_id: uuid.UUID | None = None,
    intake_source_type: str = "news_article",
    intake_record_id: str | None = None,
    profile_name: str = "news_v1",
    created_at: datetime = datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
    outcome: str = AgentRunOutcome.COMPLETED.value,
) -> AgentRun:
    agent_run = _agent_run_model(
        project,
        article_id=article_id,
        intake_source_type=intake_source_type,
        intake_record_id=intake_record_id,
        profile_name=profile_name,
        created_at=created_at,
        outcome=outcome,
    )
    postgres_session.add(agent_run)
    postgres_session.flush()
    return agent_run


def _agent_run_model(
    project: Project | None,
    *,
    article_id: uuid.UUID | None = None,
    intake_source_type: str = "news_article",
    intake_record_id: str | None = None,
    profile_name: str = "news_v1",
    created_at: datetime = datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
    outcome: str = AgentRunOutcome.COMPLETED.value,
) -> AgentRun:
    return AgentRun(
        intake_source_type=intake_source_type,
        intake_record_id=intake_record_id or str(article_id or uuid.uuid4()),
        project_id=project.id if project is not None else None,
        profile_name=profile_name,
        profile_version="v1",
        triggered_by=["low_confidence"],
        provider="anthropic",
        model="claude-opus-4-7",
        prompt_version="agent_news_v1",
        input_tokens_uncached=100,
        input_tokens_cache_creation=0,
        input_tokens_cached=20,
        output_tokens=30,
        cost_usd=Decimal("0.010000"),
        latency_ms=1000,
        reasoning_trace="Agent checked attribution.",
        evidence_consulted=[],
        tool_calls_summary=[],
        outcome=outcome,
        error_text="timed out" if outcome.startswith("failed_") else None,
        budget_consumed_usd=Decimal("0.010000"),
        tool_calls_count=0,
        wallclock_seconds=1,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
    )


def _news_article(
    postgres_session: Session,
    *,
    source_slug: str | None = None,
    title: str = "Activity story",
    fetched_at: datetime = datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
) -> tuple[NewsSource, NewsArticle]:
    resolved_source_slug = source_slug or f"activity-source-{uuid.uuid4().hex}"
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == resolved_source_slug)
    ).scalar_one_or_none()
    if source is None:
        source = NewsSource(
            slug=resolved_source_slug,
            name="Activity Source",
            base_url="https://example.com",
            collector_class="PoliteNewsCollector",
        )
        postgres_session.add(source)
        postgres_session.flush()
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/{uuid.uuid4().hex}",
        url_original=f"https://example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        fetched_at=fetched_at,
        title=title,
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
    )
    postgres_session.add(article)
    postgres_session.flush()
    return source, article


def _semantic_interpretation(
    postgres_session: Session,
    project: Project,
    *,
    source_slug: str | None = None,
    field_name: str = "pipeline_status",
    reason_code: str = "news_topped_out",
    canonical_value: object = "Under Construction",
    signal_flags: dict | None = None,
    include_reference_id: bool = True,
    include_reference_index: bool = True,
    parse_status: str = NewsExtractionParseStatus.OK.value,
    created_at: datetime = datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
) -> NewsSemanticInterpretation:
    source, article = _news_article(postgres_session, source_slug=source_slug)
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="test",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash="extract-hash",
        model="claude-opus-4-7",
        model_provider="anthropic",
        output_json={},
        parse_status=NewsExtractionParseStatus.OK.value,
        created_at=created_at - timedelta(minutes=1),
    )
    postgres_session.add(extraction)
    postgres_session.flush()
    reference = NewsProjectReference(
        extraction_id=extraction.id,
        article_id=article.id,
        reference_index=0,
        candidate_name=project.project_name,
        matched_project_id=project.id,
        match_status="confirmed",
    )
    postgres_session.add(reference)
    postgres_session.flush()
    resolved_signal_flags = dict(signal_flags or {})
    metadata: dict[str, object] = {}
    if include_reference_id:
        resolved_signal_flags.setdefault("reference_id", str(reference.id))
        metadata["reference_id"] = str(reference.id)
    if include_reference_index:
        resolved_signal_flags.setdefault("reference_index", reference.reference_index)
        metadata["reference_index"] = reference.reference_index
    semantic = NewsSemanticInterpretation(
        article_id=article.id,
        extraction_id=extraction.id,
        prompt_id="interpret_v1",
        prompt_version="v1",
        prompt_hash="semantic-hash",
        model="claude-opus-4-7",
        model_provider="anthropic",
        cost_usd=Decimal("0.010000"),
        latency_ms=1000,
        output_json={
            "interpretations": [
                {
                    "field_name": field_name,
                    "canonical_value": canonical_value,
                    "confidence": "high",
                    "reason_code": reason_code,
                    "signal_flags": resolved_signal_flags,
                    "source_anchors": [],
                    "requires_corroboration": False,
                    "metadata": metadata,
                }
            ],
            "diagnostic": {},
        },
        parse_status=parse_status,
        created_at=created_at,
    )
    postgres_session.add(semantic)
    postgres_session.flush()
    return semantic


def _semantic_review_decision(
    postgres_session: Session,
    semantic: NewsSemanticInterpretation,
    project: Project,
    *,
    decision_type: str,
    canonical_value: object = "Under Construction",
    proposed_alternatives: list[object] | None = None,
    committed_at: datetime = datetime(2026, 5, 8, 11, 0, tzinfo=UTC),
) -> ReviewDecision:
    payload = {
        "origin": "semantic_pass2c",
        "field_name": "pipeline_status",
        "semantic_interpretation_id": str(semantic.id),
        "proposed_value": canonical_value,
        "semantic_interpretation": {
            "field_name": "pipeline_status",
            "canonical_value": canonical_value,
        },
    }
    if proposed_alternatives is not None:
        payload["proposed_alternatives"] = [
            {"value": value, "source_summary": "test"} for value in proposed_alternatives
        ]
    review_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.NEWS_STATUS_UNCORROBORATED,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=Priority.MEDIUM,
        field_name="pipeline_status",
        payload=payload,
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    action = {
        "accept_new": ReviewDecisionAction.ACCEPT,
        "keep_old": ReviewDecisionAction.REJECT,
        "custom": ReviewDecisionAction.OVERRIDE,
        "defer": ReviewDecisionAction.DEFER,
    }.get(
        decision_type,
        ReviewDecisionAction.ACCEPT
        if decision_type.startswith("candidate_")
        else ReviewDecisionAction.ACCEPT,
    )
    decision = ReviewDecision(
        review_item_id=review_item.id,
        action=action,
        actor="tester",
        state="committed",
        decision_type=decision_type,
        committed_at=committed_at,
        decision_value={"value": canonical_value},
    )
    postgres_session.add(decision)
    postgres_session.flush()
    return decision


def test_activity_feed_combines_change_resolution_and_agent_rows(
    postgres_session: Session,
) -> None:
    project = Project(
        canonical_address="100 Activity Way",
        raw_addresses=["100 Activity Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
        project_name="Activity Tower",
    )
    source = NewsSource(
        slug=f"activity-source-{uuid.uuid4().hex}",
        name="Activity Source",
        base_url="https://example.com",
        collector_class="PoliteNewsCollector",
    )
    postgres_session.add_all([project, source])
    postgres_session.flush()
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/activity-story",
        url_original="https://example.com/activity-story",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        fetched_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
        title="Activity story",
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
    )
    review_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        status=ReviewItemStatus.OPEN,
        priority=Priority.MEDIUM,
        field_name="pipeline_status",
        payload={},
    )
    postgres_session.add_all([article, review_item])
    postgres_session.flush()
    agent_run = AgentRun(
        intake_source_type="news_article",
        intake_record_id=str(article.id),
        project_id=project.id,
        profile_name="news_v1",
        profile_version="v1",
        triggered_by=["material_contradiction"],
        provider="anthropic",
        model="claude-opus-4-7",
        prompt_version="agent_news_v1",
        input_tokens_uncached=100,
        input_tokens_cache_creation=0,
        input_tokens_cached=20,
        output_tokens=30,
        cost_usd=Decimal("0.012345"),
        latency_ms=1000,
        reasoning_trace="Agent checked attribution.",
        evidence_consulted=[],
        tool_calls_summary=[],
        outcome=AgentRunOutcome.COMPLETED.value,
        budget_consumed_usd=Decimal("0.012345"),
        tool_calls_count=1,
        wallclock_seconds=2,
        started_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
        completed_at=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
        created_at=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
    )
    postgres_session.add(agent_run)
    postgres_session.flush()
    change = ChangeLog(
        project_id=project.id,
        review_item_id=review_item.id,
        timestamp=datetime(2026, 5, 8, 10, 3, tzinfo=UTC),
        source="urbanize_la",
        field="pipeline_status",
        old_value="Approved",
        new_value="Under Construction",
        change_type=ChangeType.RESEARCHER_CONFIRMED,
        priority=Priority.HIGH,
        reviewed_by="researcher",
        reviewed_by_user_id=uuid.uuid4(),
        reviewed_by_email="researcher@example.com",
    )
    resolution = ResolutionLog(
        project_id=project.id,
        field="total_units",
        current_value=90,
        resolved_value=100,
        evidence_ids=[],
        rule_applied="most_recent_wins",
        confidence=StatusConfidence.HIGH,
        created_at=datetime(2026, 5, 8, 10, 4, tzinfo=UTC),
    )
    postgres_session.add_all([change, resolution])
    postgres_session.flush()
    postgres_session.add(
        AgentRunReviewItem(agent_run_id=agent_run.id, review_item_id=review_item.id)
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        project_id=project.id,
        limit=10,
    )

    assert [event.event_type for event in response.events] == [
        "resolution",
        "change",
        "agent",
    ]
    agent_event = next(event for event in response.events if event.event_type == "agent")
    assert agent_event.article is not None
    assert agent_event.article.fetched_at == "2026-05-08T10:00:00+00:00"
    assert agent_event.intake_summary is not None
    assert agent_event.intake_summary.kind == "news_article"
    assert agent_event.intake_summary.article == agent_event.article
    assert agent_event.agent_created_at == "2026-05-08T10:02:00+00:00"
    assert agent_event.review_item_ids == [review_item.id]
    assert agent_event.cost_usd == 0.012345


def test_activity_feed_agent_event_exposes_generic_non_news_intake_summary(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "175 Permit Activity Way")
    agent_run = _agent_run(
        postgres_session,
        project,
        intake_source_type="ladbs_permit",
        intake_record_id="2026LA12345",
        profile_name="permit_v1",
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        project_id=project.id,
        limit=10,
    )

    event = response.events[0]
    assert event.id == f"agent:{agent_run.id}"
    assert event.article is None
    assert event.intake_summary is not None
    assert event.intake_summary.kind == "ladbs_permit"
    assert event.intake_summary.label == "LADBS permit"


def test_activity_feed_non_news_orphan_agent_keeps_intake_summary(
    postgres_session: Session,
) -> None:
    agent_run = _agent_run(
        postgres_session,
        None,
        intake_source_type="ladbs_permit",
        intake_record_id="2026LA67890",
        profile_name="permit_v1",
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        event_type="agent",
        limit=10,
    )

    event = next(item for item in response.events if item.id == f"agent:{agent_run.id}")
    assert event.project is None
    assert event.article is None
    assert event.intake_summary is not None
    assert event.intake_summary.kind == "ladbs_permit"
    assert event.intake_summary.label == "LADBS permit"


def test_activity_feed_news_agent_missing_article_keeps_intake_discriminator(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "176 Missing Article Activity Way")
    agent_run = _agent_run(postgres_session, project)

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        project_id=project.id,
        limit=10,
    )

    event = response.events[0]
    assert event.id == f"agent:{agent_run.id}"
    assert event.article is None
    assert event.intake_summary is not None
    assert event.intake_summary.kind == "news_article"
    assert event.intake_summary.label == "News article"


def test_activity_feed_agent_view_filters_to_agent_rows(postgres_session: Session) -> None:
    project = Project(
        canonical_address="200 Activity Way",
        raw_addresses=["200 Activity Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.APPROVED,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add_all(
        [
            ResolutionLog(
                project_id=project.id,
                field="total_units",
                current_value=90,
                resolved_value=100,
                evidence_ids=[],
                rule_applied="most_recent_wins",
                confidence=StatusConfidence.HIGH,
            ),
            AgentRun(
                intake_source_type="news_article",
                intake_record_id=str(uuid.uuid4()),
                project_id=project.id,
                profile_name="news_v1",
                profile_version="v1",
                triggered_by=["low_confidence"],
                provider="anthropic",
                model="claude-opus-4-7",
                prompt_version="agent_news_v1",
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=20,
                output_tokens=30,
                cost_usd=Decimal("0.010000"),
                latency_ms=1000,
                evidence_consulted=[],
                tool_calls_summary=[],
                outcome=AgentRunOutcome.COMPLETED.value,
                budget_consumed_usd=Decimal("0.010000"),
                tool_calls_count=0,
                wallclock_seconds=1,
                started_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
                completed_at=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
                created_at=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
            ),
        ]
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="agent",
        project_id=project.id,
        limit=10,
    )

    assert [event.event_type for event in response.events] == ["agent"]


def test_activity_feed_auto_applied_view_excludes_review_bound_rows(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "300 Activity Way")
    _semantic_interpretation(
        postgres_session,
        project,
        created_at=datetime(2026, 5, 8, 10, 7, tzinfo=UTC),
    )
    review_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        status=ReviewItemStatus.OPEN,
        priority=Priority.MEDIUM,
        field_name="pipeline_status",
        payload={},
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    unlinked_agent = _agent_run(
        postgres_session,
        project,
        created_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
    )
    linked_agent = _agent_run(
        postgres_session,
        project,
        created_at=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
    )
    postgres_session.add(
        AgentRunReviewItem(agent_run_id=linked_agent.id, review_item_id=review_item.id)
    )
    postgres_session.add_all(
        [
            ChangeLog(
                project_id=project.id,
                review_item_id=None,
                timestamp=datetime(2026, 5, 8, 10, 3, tzinfo=UTC),
                source="inline_override",
                field="total_units",
                old_value=100,
                new_value=110,
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.LOW,
            ),
            ChangeLog(
                project_id=project.id,
                review_item_id=review_item.id,
                timestamp=datetime(2026, 5, 8, 10, 4, tzinfo=UTC),
                source="urbanize_la",
                field="pipeline_status",
                old_value="Approved",
                new_value="Under Construction",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.HIGH,
            ),
            ResolutionLog(
                project_id=project.id,
                field="affordable_units",
                current_value=10,
                resolved_value=20,
                evidence_ids=[],
                rule_applied="most_recent_wins",
                confidence=StatusConfidence.HIGH,
                created_at=datetime(2026, 5, 8, 10, 5, tzinfo=UTC),
            ),
            ResolutionLog(
                project_id=project.id,
                field="market_rate_units",
                current_value=90,
                resolved_value=90,
                evidence_ids=[],
                rule_applied="most_recent_wins",
                confidence=StatusConfidence.HIGH,
                created_at=datetime(2026, 5, 8, 10, 6, tzinfo=UTC),
            ),
        ]
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="auto_applied",
        project_id=project.id,
        limit=10,
    )

    assert [(event.event_type, event.field) for event in response.events] == [
        ("resolution", "affordable_units"),
        ("change", "total_units"),
        ("agent", None),
    ]
    assert response.events[-1].id == f"agent:{unlinked_agent.id}"


def test_activity_feed_global_order_uses_stable_tiebreak(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "350 Activity Way")
    timestamp = datetime(2026, 5, 8, 10, 0, tzinfo=UTC)
    agent_run = _agent_run(postgres_session, project, created_at=timestamp)
    change = ChangeLog(
        project_id=project.id,
        timestamp=timestamp,
        source="urbanize_la",
        field="pipeline_status",
        old_value="Approved",
        new_value="Under Construction",
        change_type=ChangeType.RESEARCHER_CONFIRMED,
        priority=Priority.HIGH,
    )
    resolution = ResolutionLog(
        project_id=project.id,
        field="total_units",
        current_value=90,
        resolved_value=100,
        evidence_ids=[],
        rule_applied="most_recent_wins",
        confidence=StatusConfidence.HIGH,
        created_at=timestamp,
    )
    postgres_session.add_all([change, resolution])
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        project_id=project.id,
        limit=10,
    )

    assert [event.id for event in response.events] == [
        f"agent:{agent_run.id}",
        f"change:{change.id}",
        f"resolution:{resolution.id}",
    ]


def test_activity_feed_semantic_view_uses_pass2c_interpretation_rows(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "400 Activity Way")
    semantic = _semantic_interpretation(
        postgres_session,
        project,
        source_slug=f"semantic-source-{uuid.uuid4().hex}",
    )
    postgres_session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=datetime(2026, 5, 8, 10, 3, tzinfo=UTC),
            source="urbanize_la",
            field="pipeline_status",
            old_value="Approved",
            new_value="Under Construction",
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.HIGH,
        )
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="semantic",
        project_id=project.id,
        limit=10,
    )

    assert [(event.event_type, event.source, event.field) for event in response.events] == [
        ("semantic", "semantic.news_v1", "pipeline_status")
    ]
    assert response.events[0].id == f"semantic:{semantic.id}:0"
    assert response.events[0].project is not None
    assert response.events[0].project.id == project.id
    assert response.events[0].article is not None
    assert response.events[0].intake_summary is not None
    assert response.events[0].intake_summary.kind == "news_article"
    assert response.events[0].intake_summary.article == response.events[0].article
    assert response.events[0].detail["reason_code"] == "news_topped_out"


def test_activity_feed_semantic_source_filter_matches_article_source(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "425 Activity Way")
    source_slug = f"semantic-source-{uuid.uuid4().hex}"
    _semantic_interpretation(postgres_session, project, source_slug=source_slug)
    _semantic_interpretation(
        postgres_session,
        project,
        source_slug=f"other-semantic-source-{uuid.uuid4().hex}",
        created_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        event_type="semantic",
        source=source_slug,
        project_id=project.id,
        limit=10,
    )

    assert len(response.events) == 1
    assert response.events[0].article is not None
    assert response.events[0].article.source_slug == source_slug


def test_activity_feed_semantic_row_fans_out_multiple_interpretations(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "450 Activity Way")
    semantic = _semantic_interpretation(postgres_session, project)
    first = semantic.output_json["interpretations"][0]
    second = {
        **first,
        "field_name": "total_units",
        "canonical_value": 100,
        "reason_code": "news_total_units_explicit",
    }
    semantic.output_json = {
        **semantic.output_json,
        "interpretations": [first, second],
    }
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        event_type="semantic",
        project_id=project.id,
        limit=10,
    )

    assert [event.id for event in response.events] == [
        f"semantic:{semantic.id}:0",
        f"semantic:{semantic.id}:1",
    ]
    assert [event.field for event in response.events] == [
        "pipeline_status",
        "total_units",
    ]


def test_activity_feed_semantic_reference_index_only_links_project(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "460 Activity Way")
    _semantic_interpretation(
        postgres_session,
        project,
        include_reference_id=False,
        include_reference_index=True,
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="semantic",
        project_id=project.id,
        limit=10,
    )

    assert len(response.events) == 1
    assert response.events[0].project is not None
    assert response.events[0].project.id == project.id


def test_activity_feed_semantic_single_reference_fallback_links_project(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "470 Activity Way")
    _semantic_interpretation(
        postgres_session,
        project,
        include_reference_id=False,
        include_reference_index=False,
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="semantic",
        project_id=project.id,
        limit=10,
    )

    assert len(response.events) == 1
    assert response.events[0].project is not None
    assert response.events[0].project.id == project.id


def test_activity_feed_semantic_unresolved_multi_reference_keeps_project_null(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "480 Activity Way")
    other_project = _project(postgres_session, "481 Activity Way")
    source_slug = f"semantic-unresolved-source-{uuid.uuid4().hex}"
    semantic = _semantic_interpretation(
        postgres_session,
        project,
        source_slug=source_slug,
        include_reference_id=False,
        include_reference_index=False,
    )
    postgres_session.add(
        NewsProjectReference(
            extraction_id=semantic.extraction_id,
            article_id=semantic.article_id,
            reference_index=1,
            candidate_name=other_project.project_name,
            matched_project_id=other_project.id,
            match_status="confirmed",
        )
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="semantic",
        source=source_slug,
        limit=10,
    )

    assert len(response.events) == 1
    assert response.events[0].project is None


def test_activity_feed_semantic_parse_error_rows_are_excluded(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "490 Activity Way")
    _semantic_interpretation(
        postgres_session,
        project,
        parse_status=NewsExtractionParseStatus.PARSE_ERROR.value,
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        event_type="semantic",
        project_id=project.id,
        limit=10,
    )

    assert response.events == []


def test_activity_feed_source_filter_matches_agent_article_source(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "500 Activity Way")
    source = NewsSource(
        slug=f"activity-source-{uuid.uuid4().hex}",
        name="Activity Source",
        base_url="https://example.com",
        collector_class="PoliteNewsCollector",
    )
    postgres_session.add(source)
    postgres_session.flush()
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/activity-source-filter",
        url_original="https://example.com/activity-source-filter",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        fetched_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
        title="Activity source filter",
        ingest_method=ScrapeJobKind.NEWS_SCRAPE.value,
    )
    postgres_session.add(article)
    postgres_session.flush()
    _agent_run(
        postgres_session,
        project,
        article_id=article.id,
        created_at=datetime(2026, 5, 8, 8, 0, tzinfo=UTC),
    )
    postgres_session.add_all(
        [
            _agent_run_model(
                project,
                created_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC) + timedelta(seconds=index),
            )
            for index in range(MAX_INTERNAL_LIMIT + 1)
        ]
    )
    postgres_session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
            source=source.slug,
            field="total_units",
            old_value=100,
            new_value=110,
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.MEDIUM,
        )
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        source=source.slug,
        project_id=project.id,
        limit=10,
    )

    assert [event.event_type for event in response.events] == ["change", "agent"]


def test_activity_feed_resolution_view_filters_noop_rows(postgres_session: Session) -> None:
    project = _project(postgres_session, "550 Activity Way")
    postgres_session.add_all(
        [
            ResolutionLog(
                project_id=project.id,
                field="market_rate_units",
                current_value=90,
                resolved_value=90,
                evidence_ids=[],
                rule_applied="most_recent_wins",
                confidence=StatusConfidence.HIGH,
                created_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
            ),
            ResolutionLog(
                project_id=project.id,
                field="total_units",
                current_value=90,
                resolved_value=100,
                evidence_ids=[],
                rule_applied="most_recent_wins",
                confidence=StatusConfidence.HIGH,
                created_at=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
            ),
        ]
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        event_type="resolution",
        project_id=project.id,
        limit=10,
    )

    assert [(event.event_type, event.field) for event in response.events] == [
        ("resolution", "total_units")
    ]


def test_activity_feed_combined_filters_keep_expected_rows(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "575 Activity Way")
    review_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        status=ReviewItemStatus.OPEN,
        priority=Priority.MEDIUM,
        field_name="pipeline_status",
        payload={},
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    postgres_session.add_all(
        [
            ChangeLog(
                project_id=project.id,
                review_item_id=None,
                timestamp=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
                source="urbanize_la",
                field="pipeline_status",
                old_value="Approved",
                new_value="Under Construction",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.HIGH,
            ),
            ChangeLog(
                project_id=project.id,
                review_item_id=None,
                timestamp=datetime(2026, 5, 8, 10, 2, tzinfo=UTC),
                source="urbanize_la",
                field="total_units",
                old_value=100,
                new_value=120,
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.MEDIUM,
            ),
            ChangeLog(
                project_id=project.id,
                review_item_id=review_item.id,
                timestamp=datetime(2026, 5, 8, 10, 3, tzinfo=UTC),
                source="urbanize_la",
                field="pipeline_status",
                old_value="Under Construction",
                new_value="Complete",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.HIGH,
            ),
            ChangeLog(
                project_id=project.id,
                review_item_id=None,
                timestamp=datetime(2026, 5, 7, 10, 1, tzinfo=UTC),
                source="urbanize_la",
                field="pipeline_status",
                old_value="Proposed",
                new_value="Approved",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.MEDIUM,
            ),
            ChangeLog(
                project_id=project.id,
                review_item_id=None,
                timestamp=datetime(2026, 5, 8, 10, 4, tzinfo=UTC),
                source="costar",
                field="pipeline_status",
                old_value="Approved",
                new_value="Under Construction",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.MEDIUM,
            ),
        ]
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="auto_applied",
        source="urbanize_la",
        field="pipeline_status",
        project_id=project.id,
        from_date=date(2026, 5, 8),
        to_date=date(2026, 5, 8),
        limit=10,
    )

    assert [(event.source, event.field, event.new_value) for event in response.events] == [
        ("urbanize_la", "pipeline_status", "Under Construction")
    ]


def test_activity_feed_filters_by_market_and_jurisdiction(
    postgres_session: Session,
) -> None:
    la_project = _project(
        postgres_session,
        "590 Activity Way",
        market="los_angeles",
        jurisdiction="city_of_los_angeles",
    )
    other_project = _project(
        postgres_session,
        "591 Activity Way",
        market="orange_county",
        jurisdiction="city_of_anaheim",
    )
    postgres_session.add_all(
        [
            ChangeLog(
                project_id=la_project.id,
                timestamp=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
                source="urbanize_la",
                field="pipeline_status",
                old_value="Approved",
                new_value="Under Construction",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.HIGH,
            ),
            ChangeLog(
                project_id=other_project.id,
                timestamp=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
                source="urbanize_la",
                field="pipeline_status",
                old_value="Approved",
                new_value="Under Construction",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.HIGH,
            ),
        ]
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        event_type="change",
        market="los_angeles",
        jurisdiction="city_of_los_angeles",
        limit=10,
    )

    assert len(response.events) == 1
    assert response.events[0].project is not None
    assert response.events[0].project.id == la_project.id


def test_activity_feed_filters_by_date_and_actor(postgres_session: Session) -> None:
    project = _project(postgres_session, "600 Activity Way")
    reviewer_id = uuid.uuid4()
    postgres_session.add_all(
        [
            ChangeLog(
                project_id=project.id,
                timestamp=datetime(2026, 5, 7, 10, 0, tzinfo=UTC),
                source="inline_override",
                field="total_units",
                old_value=100,
                new_value=101,
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.LOW,
                reviewed_by="researcher",
                reviewed_by_user_id=reviewer_id,
                reviewed_by_email="researcher@example.com",
            ),
            ChangeLog(
                project_id=project.id,
                timestamp=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
                source="inline_override",
                field="total_units",
                old_value=101,
                new_value=102,
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.LOW,
                reviewed_by="other",
                reviewed_by_user_id=uuid.uuid4(),
                reviewed_by_email="other@example.com",
            ),
        ]
    )
    postgres_session.flush()

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        actor="researcher@example.com",
        project_id=project.id,
        from_date=date(2026, 5, 7),
        to_date=date(2026, 5, 7),
        limit=10,
    )

    assert [event.new_value for event in response.events] == [101]

    response_by_uuid = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        actor=str(reviewer_id),
        project_id=project.id,
        limit=10,
    )
    assert [event.new_value for event in response_by_uuid.events] == [101]


def test_activity_feed_failed_agent_title_is_outcome_aware(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "700 Activity Way")
    _agent_run(
        postgres_session,
        project,
        outcome=AgentRunOutcome.FAILED_TIMEOUT.value,
        created_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="agent",
        project_id=project.id,
        limit=10,
    )

    assert response.events[0].title == "Agent failed: Timeout"


def test_activity_feed_killed_agent_title_is_outcome_aware(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "725 Activity Way")
    _agent_run(
        postgres_session,
        project,
        outcome=AgentRunOutcome.KILLED_BY_SWITCH.value,
        created_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
    )

    response = list_activity_events(
        user=_auth_user(),
        session=postgres_session,
        view="agent",
        project_id=project.id,
        limit=10,
    )

    assert response.events[0].title == "Agent killed by switch"


def test_activity_semantic_metrics_aggregate_gap_and_unmappable_rates(
    postgres_session: Session,
) -> None:
    project = _project(postgres_session, "750 Activity Way")
    source_slug = f"semantic-metrics-source-{uuid.uuid4().hex}"
    _semantic_interpretation(
        postgres_session,
        project,
        source_slug=source_slug,
        reason_code="news_status_unmappable",
        signal_flags={"glossary_gap_observed": True},
    )
    _semantic_interpretation(
        postgres_session,
        project,
        source_slug=source_slug,
        reason_code="news_status_unmappable",
        signal_flags={"glossary_gap_observed": False},
        created_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
    )

    response = list_activity_semantic_metrics(
        user=_auth_user(),
        session=postgres_session,
        source=source_slug,
        field="pipeline_status",
    )

    assert len(response.metrics) == 1
    metric = response.metrics[0]
    assert metric.market == "los_angeles"
    assert metric.source_slug == source_slug
    assert metric.field_name == "pipeline_status"
    assert metric.reason_code == "news_status_unmappable"
    assert metric.total_count == 2
    assert metric.glossary_gap_count == 1
    assert metric.unmappable_count == 2
    assert metric.glossary_gap_rate == 0.5
    assert metric.unmappable_rate == 1.0
    assert response.thresholds["glossary_gap_rate"] == 0.15
    assert response.thresholds["unmappable_rate"] == 0.05


def test_activity_semantic_metrics_count_reviewer_rejection_rate(
    postgres_session: Session,
) -> None:
    projects = [
        _project(postgres_session, f"760 Activity Way Unit {index}")
        for index in range(6)
    ]
    source_slug = f"semantic-rejection-source-{uuid.uuid4().hex}"
    semantics = [
        _semantic_interpretation(
            postgres_session,
            projects[index],
            source_slug=source_slug,
            reason_code="news_status_uncorroborated_high_quality_permit_jurisdiction",
            created_at=datetime(2026, 5, 8, 10, index, tzinfo=UTC),
        )
        for index in range(6)
    ]
    _semantic_review_decision(
        postgres_session,
        semantics[0],
        projects[0],
        decision_type="accept_new",
    )
    _semantic_review_decision(
        postgres_session,
        semantics[1],
        projects[1],
        decision_type="keep_old",
    )
    _semantic_review_decision(
        postgres_session,
        semantics[2],
        projects[2],
        decision_type="custom",
    )
    _semantic_review_decision(
        postgres_session,
        semantics[3],
        projects[3],
        decision_type="defer",
    )
    _semantic_review_decision(
        postgres_session,
        semantics[4],
        projects[4],
        decision_type="candidate_1",
        proposed_alternatives=["Under Construction"],
    )
    _semantic_review_decision(
        postgres_session,
        semantics[5],
        projects[5],
        decision_type="candidate_1",
        proposed_alternatives=["Approved"],
    )

    response = list_activity_semantic_metrics(
        user=_auth_user(),
        session=postgres_session,
        source=source_slug,
        field="pipeline_status",
    )

    assert len(response.metrics) == 1
    metric = response.metrics[0]
    assert metric.total_count == 6
    assert metric.reviewer_decision_count == 5
    assert metric.reviewer_rejection_count == 3
    assert metric.reviewer_rejection_rate == 0.6
    assert response.thresholds["reviewer_rejection_sigma"] == 2.0


def test_activity_semantic_metrics_market_filter_excludes_other_markets(
    postgres_session: Session,
) -> None:
    la_project = _project(
        postgres_session,
        "775 Activity Way",
        market="los_angeles",
    )
    other_project = _project(
        postgres_session,
        "776 Activity Way",
        market="orange_county",
    )
    source_slug = f"semantic-market-source-{uuid.uuid4().hex}"
    _semantic_interpretation(
        postgres_session,
        la_project,
        source_slug=source_slug,
        reason_code="news_status_unmappable",
        signal_flags={"glossary_gap_observed": True},
    )
    _semantic_interpretation(
        postgres_session,
        other_project,
        source_slug=source_slug,
        reason_code="news_status_unmappable",
        signal_flags={"glossary_gap_observed": True},
        created_at=datetime(2026, 5, 8, 10, 1, tzinfo=UTC),
    )

    response = list_activity_semantic_metrics(
        user=_auth_user(),
        session=postgres_session,
        source=source_slug,
        market="los_angeles",
    )

    assert len(response.metrics) == 1
    assert response.metrics[0].market == "los_angeles"
    assert response.metrics[0].total_count == 1
