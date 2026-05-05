from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    NewsArticle,
    NewsExtraction,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsSource,
    NewsTriageStatus,
    Priority,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
)


def _ensure_agent_run_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "agent_runs",
        "agent_run_review_items",
        "news_sources",
        "news_articles",
        "news_extractions",
        "review_items",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply AGENT.2 agent-run migrations before running tests: {missing}")

    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    required_columns = {"evidence_consulted", "tool_calls_summary", "completed_at"}
    missing_columns = required_columns - columns
    if missing_columns:
        pytest.skip(
            "Apply migration 202605050030 before running agent-run tests: "
            f"{sorted(missing_columns)}"
        )


def _agent_run_kwargs(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "intake_source_type": "news_article",
        "intake_record_id": str(uuid.uuid4()),
        "profile_name": "news_v1",
        "profile_version": "1.0.0",
        "triggered_by": ["new_candidate"],
        "provider": "anthropic",
        "model": "claude-opus-4-7",
        "prompt_version": "agent_news_v1",
        "input_tokens_uncached": 100,
        "input_tokens_cache_creation": 0,
        "input_tokens_cached": 0,
        "output_tokens": 25,
        "cost_usd": 0.001,
        "latency_ms": 1200,
        "outcome": AgentRunOutcome.COMPLETED.value,
        "budget_consumed_usd": 0.001,
        "tool_calls_count": 0,
        "wallclock_seconds": 2,
        "started_at": now,
        "completed_at": now,
    }
    defaults.update(overrides)
    return defaults


def test_agent_run_round_trip_and_review_join(postgres_session: Session) -> None:
    _ensure_agent_run_tables(postgres_session)

    review_item = ReviewItem(
        item_type=ReviewItemType.POSSIBLE_MATCH,
        status=ReviewItemStatus.OPEN,
        priority=Priority.MEDIUM,
    )
    agent_run = AgentRun(**_agent_run_kwargs())
    postgres_session.add_all([review_item, agent_run])
    postgres_session.flush()

    link = AgentRunReviewItem(agent_run_id=agent_run.id, review_item_id=review_item.id)
    postgres_session.add(link)
    postgres_session.flush()
    postgres_session.refresh(agent_run)

    assert agent_run.evidence_consulted == []
    assert agent_run.tool_calls_summary == []
    joined = postgres_session.execute(
        select(AgentRunReviewItem).where(
            AgentRunReviewItem.agent_run_id == agent_run.id,
            AgentRunReviewItem.review_item_id == review_item.id,
        )
    ).scalar_one()
    assert joined.agent_run_id == agent_run.id


def test_agent_run_rejects_empty_triggered_by(postgres_session: Session) -> None:
    _ensure_agent_run_tables(postgres_session)

    postgres_session.add(AgentRun(**_agent_run_kwargs(triggered_by=[])))

    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_agent_run_rejects_failed_outcome_without_error_text(
    postgres_session: Session,
) -> None:
    _ensure_agent_run_tables(postgres_session)

    postgres_session.add(AgentRun(**_agent_run_kwargs(outcome=AgentRunOutcome.FAILED_ERROR.value)))

    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_agent_run_rejects_negative_counters(postgres_session: Session) -> None:
    _ensure_agent_run_tables(postgres_session)

    postgres_session.add(AgentRun(**_agent_run_kwargs(input_tokens_uncached=-1)))

    with pytest.raises(IntegrityError):
        postgres_session.flush()


@pytest.mark.parametrize("field_name", ["evidence_consulted", "tool_calls_summary"])
def test_agent_run_rejects_non_array_jsonb(
    postgres_session: Session,
    field_name: str,
) -> None:
    _ensure_agent_run_tables(postgres_session)

    postgres_session.add(AgentRun(**_agent_run_kwargs(**{field_name: {"not": "an array"}})))

    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_agent_run_survives_deleted_news_extraction(postgres_session: Session) -> None:
    _ensure_agent_run_tables(postgres_session)

    source = NewsSource(
        slug=f"agent-run-schema-{uuid.uuid4().hex}",
        name="Agent Run Schema Test",
        base_url="https://example.com",
        collector_class="test",
    )
    article_id = uuid.uuid4()
    article = NewsArticle(
        id=article_id,
        source=source,
        url_canonical=f"https://example.com/agent-run-schema-{uuid.uuid4().hex}",
        url_original="https://example.com/agent-run-schema",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        ingest_method="news_paste_a_link",
    )
    extraction = NewsExtraction(
        article=article,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="test",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash=uuid.uuid4().hex,
        model="claude-opus-4-7",
    )
    agent_run = AgentRun(
        **_agent_run_kwargs(intake_record_id=str(article_id), intake_extraction=extraction)
    )
    postgres_session.add(agent_run)
    postgres_session.flush()

    extraction_id = extraction.id
    agent_run_id = agent_run.id
    postgres_session.execute(
        text("DELETE FROM news_extractions WHERE id = :extraction_id"),
        {"extraction_id": extraction_id},
    )
    postgres_session.flush()
    postgres_session.expire_all()

    persisted = postgres_session.get(AgentRun, agent_run_id)
    assert persisted is not None
    assert persisted.intake_extraction_id is None
