from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tcg_pipeline.agents.profiles import PERMIT_AGENT_PROFILE
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


def test_permit_agent_smoke_report_summarizes_latest_source_run(
    postgres_session: Session,
) -> None:
    older_run = SourceRun(
        market="los_angeles",
        source_name="ladbs_permits",
        collection_mode="preview",
        run_timestamp=datetime(2026, 5, 9, tzinfo=UTC),
        records_pulled=1,
    )
    source_run = SourceRun(
        market="los_angeles",
        source_name="ladbs_permits",
        collection_mode="preview",
        run_timestamp=datetime(2026, 5, 10, tzinfo=UTC),
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
        required_triggers=("new_candidate", "unit_delta", "product_type_change"),
        expected_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
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
        required_triggers=("unit_delta",),
        expected_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
    )

    assert failures == [
        "Expected at least 2 permit agent runs; found 1.",
        "Missing required trigger(s): unit_delta.",
        "Unexpected outcome(s): completed.",
        "1 permit agent run(s) have no linked review item.",
    ]


def _agent_run(
    *,
    source_run: SourceRun,
    intake_record_id: str,
    triggered_by: list[str],
    outcome: str = AgentRunOutcome.KILLED_BY_SWITCH.value,
    cost_usd: Decimal = Decimal("0"),
) -> AgentRun:
    now = (source_run.run_timestamp or datetime.now(UTC)) + timedelta(minutes=1)
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
        error_text="agent_allow_live_llm=false" if outcome == "killed_by_switch" else None,
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
