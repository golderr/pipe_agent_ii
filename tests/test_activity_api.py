from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.routers.activity import list_activity_events
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    ChangeLog,
    ChangeType,
    NewsArticle,
    NewsFetchStatus,
    NewsSource,
    PipelineStatus,
    Priority,
    Project,
    ResolutionLog,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    ScrapeJobKind,
    StatusConfidence,
)


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
        user=AuthenticatedUser(
            user_id=uuid.uuid4(),
            email="allowed@example.com",
            role="authenticated",
            claims={},
        ),
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
    assert agent_event.agent_created_at == "2026-05-08T10:02:00+00:00"
    assert agent_event.review_item_ids == [review_item.id]
    assert agent_event.cost_usd == 0.012345


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
        user=AuthenticatedUser(
            user_id=uuid.uuid4(),
            email="allowed@example.com",
            role="authenticated",
            claims={},
        ),
        session=postgres_session,
        view="agent",
        project_id=project.id,
        limit=10,
    )

    assert [event.event_type for event in response.events] == ["agent"]
