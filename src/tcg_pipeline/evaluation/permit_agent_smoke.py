from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tcg_pipeline.agents.profiles import PERMIT_AGENT_PROFILE
from tcg_pipeline.db.models import AgentRun, AgentRunReviewItem, SourceRun


@dataclass(frozen=True, slots=True)
class PermitAgentSmokeRun:
    agent_run_id: uuid.UUID
    intake_record_id: str
    project_id: uuid.UUID | None
    triggered_by: tuple[str, ...]
    outcome: str
    cost_usd: Decimal
    review_item_count: int
    error_text: str | None


@dataclass(frozen=True, slots=True)
class PermitAgentSmokeReport:
    source_run_id: uuid.UUID | None
    source_name: str | None
    market: str | None
    run_timestamp: datetime | None
    records_pulled: int | None
    agent_run_count: int
    outcome_counts: dict[str, int]
    trigger_counts: dict[str, int]
    total_cost_usd: Decimal
    missing_review_link_count: int
    runs: tuple[PermitAgentSmokeRun, ...]


def build_permit_agent_smoke_report(
    session: Session,
    *,
    source_run_id: uuid.UUID | None = None,
    market: str = "los_angeles",
    source_name: str = "ladbs_permits",
) -> PermitAgentSmokeReport:
    source_run = (
        session.get(SourceRun, source_run_id)
        if source_run_id is not None
        else _latest_source_run(session, market=market, source_name=source_name)
    )
    if source_run is None:
        raise ValueError(
            f"No source run found for market={market!r}, source_name={source_name!r}."
        )
    agent_runs = (
        session.execute(
            select(AgentRun)
            .where(
                AgentRun.profile_name == PERMIT_AGENT_PROFILE.name,
                AgentRun.source_run_id == source_run.id,
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        .scalars()
        .all()
    )
    review_link_counts = _review_link_counts(session, agent_run_ids=[run.id for run in agent_runs])
    runs = tuple(
        PermitAgentSmokeRun(
            agent_run_id=run.id,
            intake_record_id=run.intake_record_id,
            project_id=run.project_id,
            triggered_by=tuple(str(trigger) for trigger in run.triggered_by),
            outcome=str(run.outcome),
            cost_usd=_decimal(run.cost_usd),
            review_item_count=review_link_counts.get(run.id, 0),
            error_text=run.error_text,
        )
        for run in agent_runs
    )
    outcome_counts = Counter(run.outcome for run in runs)
    trigger_counts = Counter(trigger for run in runs for trigger in run.triggered_by)
    total_cost = sum((run.cost_usd for run in runs), Decimal("0"))
    return PermitAgentSmokeReport(
        source_run_id=source_run.id,
        source_name=source_run.source_name,
        market=source_run.market,
        run_timestamp=source_run.run_timestamp,
        records_pulled=source_run.records_pulled,
        agent_run_count=len(runs),
        outcome_counts=dict(sorted(outcome_counts.items())),
        trigger_counts=dict(sorted(trigger_counts.items())),
        total_cost_usd=total_cost,
        missing_review_link_count=sum(1 for run in runs if run.review_item_count == 0),
        runs=runs,
    )


def validate_permit_agent_smoke_report(
    report: PermitAgentSmokeReport,
    *,
    min_agent_runs: int = 1,
    required_triggers: tuple[str, ...] = (),
    expected_outcomes: tuple[str, ...] = (),
    require_review_links: bool = True,
) -> list[str]:
    failures: list[str] = []
    if report.agent_run_count < min_agent_runs:
        failures.append(
            f"Expected at least {min_agent_runs} permit agent runs; found "
            f"{report.agent_run_count}."
        )
    missing_triggers = sorted(set(required_triggers) - set(report.trigger_counts))
    if missing_triggers:
        failures.append(f"Missing required trigger(s): {', '.join(missing_triggers)}.")
    if expected_outcomes:
        unexpected = sorted(set(report.outcome_counts) - set(expected_outcomes))
        if unexpected:
            failures.append(f"Unexpected outcome(s): {', '.join(unexpected)}.")
    if require_review_links and report.missing_review_link_count:
        failures.append(
            f"{report.missing_review_link_count} permit agent run(s) have no linked review item."
        )
    return failures


def permit_agent_smoke_report_to_dict(report: PermitAgentSmokeReport) -> dict[str, Any]:
    return {
        "source_run_id": str(report.source_run_id) if report.source_run_id else None,
        "source_name": report.source_name,
        "market": report.market,
        "run_timestamp": report.run_timestamp.isoformat() if report.run_timestamp else None,
        "records_pulled": report.records_pulled,
        "agent_run_count": report.agent_run_count,
        "outcome_counts": report.outcome_counts,
        "trigger_counts": report.trigger_counts,
        "total_cost_usd": str(report.total_cost_usd),
        "missing_review_link_count": report.missing_review_link_count,
        "runs": [
            {
                "agent_run_id": str(run.agent_run_id),
                "intake_record_id": run.intake_record_id,
                "project_id": str(run.project_id) if run.project_id else None,
                "triggered_by": list(run.triggered_by),
                "outcome": run.outcome,
                "cost_usd": str(run.cost_usd),
                "review_item_count": run.review_item_count,
                "error_text": run.error_text,
            }
            for run in report.runs
        ],
    }


def _latest_source_run(
    session: Session,
    *,
    market: str,
    source_name: str,
) -> SourceRun | None:
    return session.execute(
        select(SourceRun)
        .where(SourceRun.market == market, SourceRun.source_name == source_name)
        .order_by(SourceRun.run_timestamp.desc(), SourceRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _review_link_counts(
    session: Session,
    *,
    agent_run_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not agent_run_ids:
        return {}
    rows = session.execute(
        select(
            AgentRunReviewItem.agent_run_id,
            func.count(AgentRunReviewItem.review_item_id),
        )
        .where(AgentRunReviewItem.agent_run_id.in_(agent_run_ids))
        .group_by(AgentRunReviewItem.agent_run_id)
    ).all()
    return {row.agent_run_id: int(row[1]) for row in rows}


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or "0"))
