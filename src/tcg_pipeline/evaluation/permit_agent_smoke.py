from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from tcg_pipeline.agents.profiles import PERMIT_AGENT_PROFILE, AgentTrigger
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunReviewItem,
    ReviewItem,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.source_tiers import get_logical_source_type


@dataclass(frozen=True, slots=True)
class PermitAgentSmokeRun:
    agent_run_id: uuid.UUID
    intake_record_id: str
    project_id: uuid.UUID | None
    triggered_by: tuple[str, ...]
    outcome: str
    cost_usd: Decimal
    review_item_count: int
    review_item_type_counts: dict[str, int]
    status_regression_review_item_count: int
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
    review_item_type_counts: dict[str, int]
    status_regression_agent_run_count: int
    status_regression_review_item_count: int
    status_regression_duplicate_project_count: int
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
    source_type = get_logical_source_type(source_run.source_name)
    if source_type != PERMIT_AGENT_PROFILE.intake_source_type:
        raise ValueError(
            "Permit agent smoke reports require a LADBS permit source_run; "
            f"{source_run.id} is source_name={source_run.source_name!r} "
            f"(logical source_type={source_type!r})."
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
    agent_run_ids = [run.id for run in agent_runs]
    review_link_counts = _review_link_counts(session, agent_run_ids=agent_run_ids)
    status_regression_payloads_by_agent = _status_regression_review_payloads_by_agent(
        session,
        agent_run_ids=agent_run_ids,
    )
    runs = tuple(
        _smoke_run_for_agent_run(
            run,
            review_item_type_counts=review_link_counts.get(run.id, {}),
        )
        for run in agent_runs
    )
    outcome_counts = Counter(run.outcome for run in runs)
    trigger_counts = Counter(trigger for run in runs for trigger in run.triggered_by)
    review_item_type_counts: Counter[str] = Counter()
    for run in runs:
        review_item_type_counts.update(run.review_item_type_counts)
    status_regression_trigger = AgentTrigger.STATUS_REGRESSION_CANDIDATE.value
    status_regression_review_type = ReviewItemType.STATUS_REGRESSION_REVIEW.value
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
        review_item_type_counts=dict(sorted(review_item_type_counts.items())),
        status_regression_agent_run_count=sum(
            1 for run in runs if status_regression_trigger in run.triggered_by
        ),
        status_regression_review_item_count=review_item_type_counts.get(
            status_regression_review_type,
            0,
        ),
        status_regression_duplicate_project_count=(
            _status_regression_duplicate_project_count(
                agent_runs,
                review_payloads_by_agent=status_regression_payloads_by_agent,
            )
        ),
        total_cost_usd=total_cost,
        missing_review_link_count=sum(1 for run in runs if run.review_item_count == 0),
        runs=runs,
    )


def validate_permit_agent_smoke_report(
    report: PermitAgentSmokeReport,
    *,
    min_agent_runs: int = 1,
    max_agent_runs: int | None = None,
    required_triggers: tuple[str, ...] = (),
    required_outcomes: tuple[str, ...] = (),
    allowed_outcomes: tuple[str, ...] = (),
    min_status_regression_review_items: int = 0,
    max_status_regression_duplicate_projects: int | None = None,
    min_total_cost_usd: Decimal | None = None,
    max_total_cost_usd: Decimal | None = None,
    require_review_links: bool = True,
) -> list[str]:
    failures: list[str] = []
    if report.agent_run_count < min_agent_runs:
        failures.append(
            f"Expected at least {min_agent_runs} permit agent runs; found "
            f"{report.agent_run_count}."
        )
    if max_agent_runs is not None and report.agent_run_count > max_agent_runs:
        failures.append(
            f"Expected at most {max_agent_runs} permit agent runs; found "
            f"{report.agent_run_count}."
        )
    missing_triggers = sorted(set(required_triggers) - set(report.trigger_counts))
    if missing_triggers:
        failures.append(f"Missing required trigger(s): {', '.join(missing_triggers)}.")
    missing_outcomes = sorted(set(required_outcomes) - set(report.outcome_counts))
    if missing_outcomes:
        failures.append(f"Missing required outcome(s): {', '.join(missing_outcomes)}.")
    if allowed_outcomes:
        unexpected = sorted(set(report.outcome_counts) - set(allowed_outcomes))
        if unexpected:
            failures.append(f"Unexpected outcome(s): {', '.join(unexpected)}.")
    if report.status_regression_review_item_count < min_status_regression_review_items:
        failures.append(
            "Expected at least "
            f"{min_status_regression_review_items} linked status regression review item(s); "
            f"found {report.status_regression_review_item_count}."
        )
    if (
        max_status_regression_duplicate_projects is not None
        and report.status_regression_duplicate_project_count
        > max_status_regression_duplicate_projects
    ):
        failures.append(
            "Expected at most "
            f"{max_status_regression_duplicate_projects} projects with duplicate "
            "status_regression_candidate triggers; found "
            f"{report.status_regression_duplicate_project_count}."
        )
    if min_total_cost_usd is not None and report.total_cost_usd < min_total_cost_usd:
        failures.append(
            f"Expected total cost >= ${min_total_cost_usd}; found ${report.total_cost_usd}."
        )
    if max_total_cost_usd is not None and report.total_cost_usd > max_total_cost_usd:
        failures.append(
            f"Expected total cost <= ${max_total_cost_usd}; found ${report.total_cost_usd}."
        )
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
        "review_item_type_counts": report.review_item_type_counts,
        "status_regression_agent_run_count": report.status_regression_agent_run_count,
        "status_regression_review_item_count": report.status_regression_review_item_count,
        "status_regression_duplicate_project_count": (
            report.status_regression_duplicate_project_count
        ),
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
                "review_item_type_counts": run.review_item_type_counts,
                "status_regression_review_item_count": (
                    run.status_regression_review_item_count
                ),
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


def _smoke_run_for_agent_run(
    run: AgentRun,
    *,
    review_item_type_counts: dict[str, int],
) -> PermitAgentSmokeRun:
    sorted_review_item_type_counts = dict(sorted(review_item_type_counts.items()))
    return PermitAgentSmokeRun(
        agent_run_id=run.id,
        intake_record_id=run.intake_record_id,
        project_id=run.project_id,
        triggered_by=tuple(str(trigger) for trigger in run.triggered_by),
        outcome=str(run.outcome),
        cost_usd=_decimal(run.cost_usd),
        review_item_count=sum(sorted_review_item_type_counts.values()),
        review_item_type_counts=sorted_review_item_type_counts,
        status_regression_review_item_count=sorted_review_item_type_counts.get(
            ReviewItemType.STATUS_REGRESSION_REVIEW.value,
            0,
        ),
        error_text=run.error_text,
    )


def _review_link_counts(
    session: Session,
    *,
    agent_run_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    if not agent_run_ids:
        return {}
    rows = session.execute(
        select(
            AgentRunReviewItem.agent_run_id,
            ReviewItem.item_type,
            func.count(ReviewItem.id),
        )
        .join(ReviewItem, AgentRunReviewItem.review_item_id == ReviewItem.id)
        .where(AgentRunReviewItem.agent_run_id.in_(agent_run_ids))
        .group_by(AgentRunReviewItem.agent_run_id, ReviewItem.item_type)
    ).all()
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for row in rows:
        item_type = getattr(row.item_type, "value", str(row.item_type))
        counts.setdefault(row.agent_run_id, {})[item_type] = int(row[2])
    return counts


def _status_regression_review_payloads_by_agent(
    session: Session,
    *,
    agent_run_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[dict[str, Any], ...]]:
    if not agent_run_ids:
        return {}
    rows = session.execute(
        select(
            AgentRunReviewItem.agent_run_id,
            ReviewItem.payload,
        )
        .join(ReviewItem, AgentRunReviewItem.review_item_id == ReviewItem.id)
        .where(
            AgentRunReviewItem.agent_run_id.in_(agent_run_ids),
            ReviewItem.item_type.cast(String) == ReviewItemType.STATUS_REGRESSION_REVIEW.value,
        )
        .order_by(AgentRunReviewItem.agent_run_id.asc(), ReviewItem.updated_at.desc())
    ).all()
    payloads: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in rows:
        if isinstance(row.payload, dict):
            payloads.setdefault(row.agent_run_id, []).append(row.payload)
    return {agent_run_id: tuple(items) for agent_run_id, items in payloads.items()}


def _status_regression_duplicate_project_count(
    agent_runs: list[AgentRun],
    *,
    review_payloads_by_agent: dict[uuid.UUID, tuple[dict[str, Any], ...]],
) -> int:
    status_regression_trigger = AgentTrigger.STATUS_REGRESSION_CANDIDATE.value
    seen: dict[tuple[uuid.UUID, str, str], set[uuid.UUID]] = {}
    for run in agent_runs:
        if status_regression_trigger not in {str(trigger) for trigger in run.triggered_by or []}:
            continue
        if run.project_id is None:
            continue
        # Prefer agent_revised_verdict because dismiss outcomes often have no linked
        # review card; fall back to linked status_regression_review payloads for
        # killed-switch or deterministic review paths where no verdict was produced.
        status_pair = _status_regression_pair_from_mapping(run.agent_revised_verdict)
        if status_pair is None:
            for payload in review_payloads_by_agent.get(run.id, ()):
                status_pair = _status_regression_pair_from_mapping(payload)
                if status_pair is not None:
                    break
        if status_pair is None:
            continue
        seen.setdefault((run.project_id, status_pair[0], status_pair[1]), set()).add(run.id)
    return sum(1 for run_ids in seen.values() if len(run_ids) >= 2)


def _status_regression_pair_from_mapping(
    value: Any,
) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    current = _clean_status_text(value.get("current_status") or value.get("current_value"))
    proposed = _clean_status_text(value.get("proposed_status") or value.get("proposed_value"))
    if current is None or proposed is None:
        nested = value.get("status_regression")
        if isinstance(nested, dict):
            return _status_regression_pair_from_mapping(nested)
        return None
    return current, proposed


def _clean_status_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or "0"))
