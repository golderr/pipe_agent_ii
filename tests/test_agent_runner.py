from __future__ import annotations

import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE, AgentTrigger
from tcg_pipeline.agents.runner import (
    AgentClientResult,
    AgentRunRequest,
    IntakeRecord,
    run_agent_for_intake,
)
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    LLMCostUsage,
    Priority,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    SystemAlert,
)
from tcg_pipeline.news.costs import (
    RESERVATION_MODEL,
    RESERVATION_PASS_NAME,
    RESERVATION_PROVIDER,
    cost_date_for,
)
from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC, LLMUsage
from tcg_pipeline.settings import Settings

FIXED_NOW = datetime(2035, 1, 2, 12, 0, tzinfo=UTC)


class FakeAgentClient:
    provider = LLM_PROVIDER_ANTHROPIC
    model = DEFAULT_EXTRACTION_MODEL
    prompt_version = NEWS_AGENT_PROFILE.prompt_version

    def __init__(self) -> None:
        self.request: AgentRunRequest | None = None

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        self.request = request
        return AgentClientResult(
            outcome=AgentRunOutcome.COMPLETED.value,
            usage=LLMUsage(
                input_tokens_uncached=100,
                input_tokens_cache_creation=0,
                input_tokens_cached=0,
                output_tokens=20,
            ),
            latency_ms=1234,
            reasoning_trace="Matcher result stands after consulting accepted article context.",
            evidence_consulted=[
                {
                    "source_type": "news_article",
                    "record_id": request.intake.intake_record_id,
                    "role": "primary",
                }
            ],
            tool_calls_summary=[
                {
                    "tool": "search_articles_similar",
                    "args_summary": "same address",
                    "result_summary": "one accepted article",
                    "latency_ms": 10,
                    "output_token_count": 20,
                }
            ],
            agent_revised_verdict={"decision": "no_change"},
        )


class RaisingAgentClient(FakeAgentClient):
    def __init__(self) -> None:
        super().__init__()
        self.called = False

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        self.called = True
        raise RuntimeError("agent call failed")


class SlowAgentClient(FakeAgentClient):
    def __init__(self) -> None:
        super().__init__()
        self.called = False

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        self.called = True
        time.sleep(0.2)
        return super().run(request)


class TooManyToolsAgentClient(FakeAgentClient):
    def run(self, request: AgentRunRequest) -> AgentClientResult:
        result = super().run(request)
        return AgentClientResult(
            outcome=result.outcome,
            usage=result.usage,
            latency_ms=result.latency_ms,
            reasoning_trace=result.reasoning_trace,
            evidence_consulted=result.evidence_consulted,
            tool_calls_summary=[
                {
                    "tool": "search_articles_similar",
                    "args_summary": "same address",
                    "result_summary": "one accepted article",
                    "latency_ms": 10,
                    "output_token_count": 20,
                },
                {
                    "tool": "search_articles_by_project",
                    "args_summary": "same developer",
                    "result_summary": "one candidate article",
                    "latency_ms": 12,
                    "output_token_count": 18,
                },
            ],
            agent_revised_verdict=result.agent_revised_verdict,
        )


def _ensure_agent_runner_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "agent_runs",
        "agent_run_review_items",
        "cost_caps",
        "llm_cost_usage",
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


def _test_profile(**overrides: Any):
    defaults: dict[str, Any] = {"required_intake_fields": frozenset()}
    defaults.update(overrides)
    return replace(NEWS_AGENT_PROFILE, **defaults)


def _session_factory(postgres_session: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=postgres_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def _intake(**overrides: Any) -> IntakeRecord:
    defaults: dict[str, Any] = {
        "source_type": "news_article",
        "intake_record_id": str(uuid.uuid4()),
        "payload": {"title": "Test article"},
    }
    defaults.update(overrides)
    return IntakeRecord(**defaults)


def test_run_agent_for_intake_persists_success_and_review_link(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    review_item = ReviewItem(
        item_type=ReviewItemType.POSSIBLE_MATCH,
        status=ReviewItemStatus.OPEN,
        priority=Priority.MEDIUM,
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    profile = _test_profile(max_cost_usd=Decimal("0.01"))
    client = FakeAgentClient()
    settings = Settings(agent_enabled_for_news=True)
    factory = _session_factory(postgres_session)

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[{"status": "possible", "candidate_count": 2}],
        trigger_reasons=[AgentTrigger.NEW_CANDIDATE],
        profile=profile,
        client=client,
        produced_review_item_ids=[review_item.id],
        settings=settings,
        session_factory=factory,
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.COMPLETED.value
    assert result.cost_usd == Decimal("0.001000")
    assert client.request is not None
    assert client.request.profile.name == "news_v1"
    assert client.request.session_factory is factory
    assert client.request.settings is settings
    postgres_session.expire_all()
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.profile_name == "news_v1"
    assert agent_run.triggered_by == ["new_candidate"]
    assert agent_run.evidence_consulted[0]["role"] == "primary"
    assert agent_run.tool_calls_count == 1
    assert agent_run.matcher_original_verdict == {
        "matcher_results": [{"status": "possible", "candidate_count": 2}]
    }
    assert agent_run.agent_revised_verdict == {"decision": "no_change"}
    link = postgres_session.execute(
        select(AgentRunReviewItem).where(
            AgentRunReviewItem.agent_run_id == result.agent_run_id,
            AgentRunReviewItem.review_item_id == review_item.id,
        )
    ).scalar_one()
    assert link.review_item_id == review_item.id
    usage = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == cost_date_for(FIXED_NOW),
            LLMCostUsage.capability == "agent.news_v1",
            LLMCostUsage.provider == LLM_PROVIDER_ANTHROPIC,
            LLMCostUsage.model == DEFAULT_EXTRACTION_MODEL,
        )
    ).scalar_one()
    assert Decimal(usage.spent_usd) == Decimal("0.001000")
    reservation = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == cost_date_for(FIXED_NOW),
            LLMCostUsage.capability == RESERVATION_PASS_NAME,
            LLMCostUsage.provider == RESERVATION_PROVIDER,
            LLMCostUsage.model == RESERVATION_MODEL,
        )
    ).scalar_one()
    assert Decimal(reservation.spent_usd) == Decimal("0.000000")


def test_run_agent_for_intake_writes_kill_switch_audit_row(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    client = FakeAgentClient()

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[],
        trigger_reasons=[AgentTrigger.LOW_CONFIDENCE],
        profile=_test_profile(),
        client=client,
        settings=Settings(agent_enabled_for_news=False),
        session_factory=_session_factory(postgres_session),
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.KILLED_BY_SWITCH.value
    assert client.request is None
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.outcome == AgentRunOutcome.KILLED_BY_SWITCH.value
    assert agent_run.cost_usd == Decimal("0.000000")
    assert agent_run.error_text == "agent_enabled_for_news=false"


def test_run_agent_for_intake_writes_budget_rejection_audit_row(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    profile = _test_profile(max_cost_usd=Decimal("9999"))
    client = FakeAgentClient()

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[],
        trigger_reasons=[AgentTrigger.MATERIAL_CONTRADICTION],
        profile=profile,
        client=client,
        settings=Settings(agent_enabled_for_news=True),
        session_factory=_session_factory(postgres_session),
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.FAILED_BUDGET.value
    assert client.request is None
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.outcome == AgentRunOutcome.FAILED_BUDGET.value
    assert agent_run.error_text == "Daily cost cap rejected the agent run reservation."


def test_run_agent_for_intake_releases_reservation_on_client_error(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    profile = _test_profile(max_cost_usd=Decimal("0.01"))
    client = RaisingAgentClient()

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[],
        trigger_reasons=[AgentTrigger.PASS1_PASS2_CONFLICT],
        profile=profile,
        client=client,
        settings=Settings(agent_enabled_for_news=True),
        session_factory=_session_factory(postgres_session),
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.FAILED_ERROR.value
    assert client.called is True
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.error_text == "agent call failed"
    reservation = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == cost_date_for(FIXED_NOW),
            LLMCostUsage.capability == RESERVATION_PASS_NAME,
            LLMCostUsage.provider == RESERVATION_PROVIDER,
            LLMCostUsage.model == RESERVATION_MODEL,
        )
    ).scalar_one()
    assert Decimal(reservation.spent_usd) == Decimal("0.000000")


def test_run_agent_for_intake_writes_timeout_audit_row(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    profile = _test_profile(max_cost_usd=Decimal("0.01"), max_wallclock_seconds=0.01)
    client = SlowAgentClient()

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[],
        trigger_reasons=[AgentTrigger.PASS1_PASS2_CONFLICT],
        profile=profile,
        client=client,
        settings=Settings(agent_enabled_for_news=True),
        session_factory=_session_factory(postgres_session),
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.FAILED_TIMEOUT.value
    assert result.error_text == "exceeded 0.01s"
    assert client.called is True
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.outcome == AgentRunOutcome.FAILED_TIMEOUT.value
    reservation = postgres_session.execute(
        select(LLMCostUsage).where(
            LLMCostUsage.bucket == "news",
            LLMCostUsage.cost_date == cost_date_for(FIXED_NOW),
            LLMCostUsage.capability == RESERVATION_PASS_NAME,
            LLMCostUsage.provider == RESERVATION_PROVIDER,
            LLMCostUsage.model == RESERVATION_MODEL,
        )
    ).scalar_one()
    assert Decimal(reservation.spent_usd) == Decimal("0.000000")


def test_run_agent_for_intake_fails_when_tool_count_exceeds_profile_cap(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    profile = _test_profile(max_cost_usd=Decimal("0.01"), max_tool_calls=1)

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[],
        trigger_reasons=[AgentTrigger.MULTIPLE_DISTINCT_MENTIONS],
        profile=profile,
        client=TooManyToolsAgentClient(),
        settings=Settings(agent_enabled_for_news=True),
        session_factory=_session_factory(postgres_session),
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.FAILED_ERROR.value
    assert result.error_text == "tool call count 2 exceeded max 1"
    assert result.cost_usd == Decimal("0.001000")
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.tool_calls_count == 2
    assert agent_run.cost_usd == Decimal("0.001000")


def test_run_agent_for_intake_alerts_when_actual_cost_exceeds_per_run_cap(
    postgres_session: Session,
) -> None:
    _ensure_agent_runner_tables(postgres_session)
    profile = _test_profile(max_cost_usd=Decimal("0.0001"))

    result = run_agent_for_intake(
        _intake(),
        matcher_results=[],
        trigger_reasons=[AgentTrigger.MATERIAL_CONTRADICTION],
        profile=profile,
        client=FakeAgentClient(),
        settings=Settings(agent_enabled_for_news=True),
        session_factory=_session_factory(postgres_session),
        now=FIXED_NOW,
    )

    assert result.outcome == AgentRunOutcome.FAILED_BUDGET.value
    assert result.error_text == "cost 0.001000 exceeded per-run cap 0.0001"
    assert result.cost_usd == Decimal("0.001000")
    agent_run = postgres_session.get(AgentRun, result.agent_run_id)
    assert agent_run is not None
    assert agent_run.cost_usd == Decimal("0.001000")
    alert = postgres_session.execute(
        select(SystemAlert).where(
            SystemAlert.alert_key == "agent_news_v1_cost_overshoot",
            SystemAlert.cleared_at.is_(None),
        )
    ).scalar_one()
    assert alert.severity == "high"
    assert alert.detail["cost_usd"] == "0.001000"
    assert alert.detail["per_run_cap_usd"] == "0.0001"


def test_run_agent_for_intake_requires_news_extraction_anchor() -> None:
    with pytest.raises(ValueError, match="extraction_id"):
        run_agent_for_intake(
            _intake(),
            matcher_results=[],
            trigger_reasons=[AgentTrigger.NEW_CANDIDATE],
            client=FakeAgentClient(),
            settings=Settings(agent_enabled_for_news=True),
        )


def test_run_agent_for_intake_rejects_wrong_source_type() -> None:
    with pytest.raises(ValueError, match="expects source_type"):
        run_agent_for_intake(
            _intake(source_type="ladbs_permit"),
            matcher_results=[],
            trigger_reasons=[AgentTrigger.NEW_CANDIDATE],
            client=FakeAgentClient(),
            settings=Settings(agent_enabled_for_news=True),
        )
