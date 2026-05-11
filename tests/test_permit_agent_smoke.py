from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import tcg_pipeline.cli as cli_module
from tcg_pipeline.agents.profiles import PERMIT_AGENT_PROFILE
from tcg_pipeline.cli import app
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    Priority,
    ReviewItem,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.evaluation.permit_agent_smoke import (
    build_permit_agent_smoke_report,
    validate_permit_agent_smoke_report,
)
from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC
from tcg_pipeline.settings import Settings

runner = CliRunner()


def test_permit_agent_smoke_report_summarizes_latest_source_run(
    postgres_session: Session,
) -> None:
    base_time = datetime(2099, 5, 10, tzinfo=UTC)
    older_run = SourceRun(
        market="los_angeles",
        source_name="ladbs_permits",
        collection_mode="preview",
        run_timestamp=base_time - timedelta(days=1),
        records_pulled=1,
    )
    source_run = SourceRun(
        market="los_angeles",
        source_name="ladbs_permits",
        collection_mode="preview",
        run_timestamp=base_time,
        records_pulled=3,
    )
    postgres_session.add_all([older_run, source_run])
    postgres_session.flush()
    old_agent_run = _agent_run(
        source_run=older_run,
        intake_record_id="older-permit",
        triggered_by=["new_candidate"],
    )
    new_candidate_run = _agent_run(
        source_run=source_run,
        intake_record_id="new-candidate-permit",
        triggered_by=["new_candidate"],
    )
    contradiction_run = _agent_run(
        source_run=source_run,
        intake_record_id="contradiction-permit",
        triggered_by=["unit_delta", "product_type_change"],
        cost_usd=Decimal("0.250000"),
    )
    postgres_session.add_all([old_agent_run, new_candidate_run, contradiction_run])
    postgres_session.flush()
    _link_review_item(postgres_session, source_run=older_run, agent_run=old_agent_run)
    _link_review_item(postgres_session, source_run=source_run, agent_run=new_candidate_run)
    _link_review_item(postgres_session, source_run=source_run, agent_run=contradiction_run)
    postgres_session.flush()

    report = build_permit_agent_smoke_report(postgres_session)

    assert report.source_run_id == source_run.id
    assert report.records_pulled == 3
    assert report.agent_run_count == 2
    assert report.outcome_counts == {AgentRunOutcome.KILLED_BY_SWITCH.value: 2}
    assert report.trigger_counts == {
        "new_candidate": 1,
        "product_type_change": 1,
        "unit_delta": 1,
    }
    assert report.total_cost_usd == Decimal("0.250000")
    assert report.missing_review_link_count == 0
    assert validate_permit_agent_smoke_report(
        report,
        min_agent_runs=2,
        max_agent_runs=2,
        required_triggers=("new_candidate", "unit_delta", "product_type_change"),
        required_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
        allowed_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
        min_total_cost_usd=Decimal("0"),
        max_total_cost_usd=Decimal("0.50"),
    ) == []


def test_permit_agent_smoke_validation_reports_missing_expectations(
    postgres_session: Session,
) -> None:
    source_run = SourceRun(
        market="los_angeles",
        source_name="ladbs_permits",
        collection_mode="preview",
        run_timestamp=datetime(2026, 5, 10, tzinfo=UTC),
        records_pulled=1,
    )
    postgres_session.add(source_run)
    postgres_session.flush()
    postgres_session.add(
        _agent_run(
            source_run=source_run,
            intake_record_id="unlinked-permit",
            triggered_by=["new_candidate"],
            outcome=AgentRunOutcome.COMPLETED.value,
            cost_usd=Decimal("0.250000"),
        )
    )
    postgres_session.flush()

    report = build_permit_agent_smoke_report(
        postgres_session,
        source_run_id=source_run.id,
    )
    failures = validate_permit_agent_smoke_report(
        report,
        min_agent_runs=2,
        max_agent_runs=0,
        required_triggers=("unit_delta",),
        required_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
        allowed_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
        min_total_cost_usd=Decimal("0.30"),
        max_total_cost_usd=Decimal("0.10"),
    )

    assert failures == [
        "Expected at least 2 permit agent runs; found 1.",
        "Expected at most 0 permit agent runs; found 1.",
        "Missing required trigger(s): unit_delta.",
        "Missing required outcome(s): killed_by_switch.",
        "Unexpected outcome(s): completed.",
        "Expected total cost >= $0.30; found $0.250000.",
        "Expected total cost <= $0.10; found $0.250000.",
        "1 permit agent run(s) have no linked review item.",
    ]


def test_permit_agent_smoke_report_rejects_non_ladbs_source_run(
    postgres_session: Session,
) -> None:
    source_run = SourceRun(
        market="los_angeles",
        source_name="costar",
        collection_mode="preview",
        run_timestamp=datetime(2026, 5, 10, tzinfo=UTC),
        records_pulled=1,
    )
    postgres_session.add(source_run)
    postgres_session.flush()

    with pytest.raises(ValueError, match="require a LADBS permit source_run"):
        build_permit_agent_smoke_report(
            postgres_session,
            source_run_id=source_run.id,
        )


def test_permit_agent_smoke_cli_prints_validation_and_failed_run_details(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
    tmp_path,
) -> None:
    source_run = SourceRun(
        market="los_angeles",
        source_name="ladbs_permits",
        collection_mode="preview",
        run_timestamp=datetime(2026, 5, 10, tzinfo=UTC),
        records_pulled=2,
    )
    postgres_session.add(source_run)
    postgres_session.flush()
    completed_run = _agent_run(
        source_run=source_run,
        intake_record_id="completed-permit",
        triggered_by=["new_candidate"],
        outcome=AgentRunOutcome.COMPLETED.value,
        cost_usd=Decimal("0.060000"),
    )
    timeout_run = _agent_run(
        source_run=source_run,
        intake_record_id="timeout-permit",
        triggered_by=["unit_delta"],
        outcome=AgentRunOutcome.FAILED_TIMEOUT.value,
        cost_usd=Decimal("0.010000"),
        error_text="wallclock exceeded 300s",
    )
    escalated_run = _agent_run(
        source_run=source_run,
        intake_record_id="escalated-permit",
        triggered_by=["product_type_change"],
        outcome=AgentRunOutcome.ESCALATED.value,
        cost_usd=Decimal("0.020000"),
    )
    postgres_session.add_all([completed_run, timeout_run, escalated_run])
    postgres_session.flush()
    _link_review_item(postgres_session, source_run=source_run, agent_run=completed_run)
    _link_review_item(postgres_session, source_run=source_run, agent_run=timeout_run)
    _link_review_item(postgres_session, source_run=source_run, agent_run=escalated_run)
    postgres_session.flush()

    @contextmanager
    def fake_session_factory():
        yield postgres_session

    monkeypatch.setattr(cli_module, "get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: Settings(database_url="postgresql://user:password@example.com/tcg"),
    )
    output_path = tmp_path / "permit_smoke.json"

    result = runner.invoke(
        app,
        [
            "permit-agent-smoke-report",
            "--source-run-id",
            str(source_run.id),
            "--require-triggers",
            "new_candidate,unit_delta,product_type_change",
            "--allow-outcomes",
            "completed,failed_timeout,escalated",
            "--require-outcomes",
            "completed",
            "--min-total-cost-usd",
            "0.05",
            "--max-total-cost-usd",
            "1.00",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Outcomes: completed=1, escalated=1, failed_timeout=1" in result.output
    assert "Triggers: new_candidate=1, product_type_change=1, unit_delta=1" in result.output
    assert "Failure runs:" in result.output
    assert "failed_timeout (timeout-permit): wallclock exceeded 300s" in result.output
    assert "escalated (escalated-permit)" not in result.output
    report_json = output_path.read_text(encoding="utf-8")
    assert '"allowed_outcomes": [' in report_json
    assert '"max_total_cost_usd": "1.00"' in report_json
    assert '"failures": []' in report_json


def _agent_run(
    *,
    source_run: SourceRun,
    intake_record_id: str,
    triggered_by: list[str],
    outcome: str = AgentRunOutcome.KILLED_BY_SWITCH.value,
    cost_usd: Decimal = Decimal("0"),
    error_text: str | None = None,
) -> AgentRun:
    now = (source_run.run_timestamp or datetime.now(UTC)) + timedelta(minutes=1)
    resolved_error_text = error_text
    if resolved_error_text is None and outcome == AgentRunOutcome.KILLED_BY_SWITCH.value:
        resolved_error_text = "agent_allow_live_llm=false"
    return AgentRun(
        intake_source_type=PERMIT_AGENT_PROFILE.intake_source_type,
        intake_record_id=intake_record_id,
        source_run_id=source_run.id,
        profile_name=PERMIT_AGENT_PROFILE.name,
        profile_version=PERMIT_AGENT_PROFILE.profile_version,
        triggered_by=triggered_by,
        provider=LLM_PROVIDER_ANTHROPIC,
        model=DEFAULT_EXTRACTION_MODEL,
        prompt_version=PERMIT_AGENT_PROFILE.prompt_version,
        input_tokens_uncached=0,
        input_tokens_cache_creation=0,
        input_tokens_cached=0,
        output_tokens=0,
        cost_usd=cost_usd,
        latency_ms=0,
        evidence_consulted=[],
        tool_calls_summary=[],
        outcome=outcome,
        error_text=resolved_error_text,
        budget_consumed_usd=cost_usd,
        tool_calls_count=0,
        wallclock_seconds=0,
        started_at=now,
        completed_at=now,
    )


def _link_review_item(
    session: Session,
    *,
    source_run: SourceRun,
    agent_run: AgentRun,
) -> None:
    review_item = ReviewItem(
        source_run_id=source_run.id,
        item_type=ReviewItemType.NEW_CANDIDATE,
        priority=Priority.MEDIUM,
        payload={"source_record_id": agent_run.intake_record_id},
    )
    session.add(review_item)
    session.flush()
    session.add(
        AgentRunReviewItem(
            agent_run_id=agent_run.id,
            review_item_id=review_item.id,
        )
    )
