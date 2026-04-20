from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import DeveloperRegistry, Evidence, PipelineStatus

BASE_RATES = {
    PipelineStatus.UNDER_CONSTRUCTION: 1.00,
    PipelineStatus.APPROVED: 0.55,
    PipelineStatus.PENDING: 0.30,
    PipelineStatus.PROPOSED: 0.15,
    PipelineStatus.CONCEPTUAL: 0.08,
    PipelineStatus.STALLED: 0.03,
    PipelineStatus.INACTIVE: 0.03,
}


def compute_likelihood(
    resolved_values: dict[str, Any],
    evidence_rows: list[Evidence],
    session: Session,
) -> tuple[float, dict[str, object]]:
    status = resolved_values["pipeline_status"]
    if status == PipelineStatus.UNDER_CONSTRUCTION:
        return 1.00, {"base": 1.00, "signals_applied": [], "final": 1.00}

    base = BASE_RATES.get(status, 0.08)
    signals_applied: list[dict[str, object]] = []

    if _has_recent_activity(evidence_rows, days=90):
        signals_applied.append({"name": "recent_activity_under_90_days", "value": 0.05})
    if _has_permits_filed(evidence_rows):
        signals_applied.append({"name": "permits_filed", "value": 0.08})
    if _is_top_tier_developer(resolved_values.get("developer"), session):
        signals_applied.append({"name": "top_tier_developer", "value": 0.05})
    if _no_activity_over_period(evidence_rows, months=24):
        signals_applied.append({"name": "no_activity_over_24_months", "value": -0.20})
    elif _no_activity_over_period(evidence_rows, months=12):
        signals_applied.append({"name": "no_activity_12_to_24_months", "value": -0.10})

    adjustment = sum(float(signal["value"]) for signal in signals_applied)
    final = max(0.02, min(0.98, base + adjustment))
    return final, {"base": base, "signals_applied": signals_applied, "final": final}


def _has_recent_activity(evidence_rows: list[Evidence], *, days: int) -> bool:
    cutoff = date.today() - timedelta(days=days)
    return any(_effective_date(evidence) >= cutoff for evidence in evidence_rows)


def _has_permits_filed(evidence_rows: list[Evidence]) -> bool:
    return any(evidence.source_type == "ladbs_permit" for evidence in evidence_rows)


def _no_activity_over_period(evidence_rows: list[Evidence], *, months: int) -> bool:
    if not evidence_rows:
        return True
    cutoff = _months_ago(months)
    return max(_effective_date(evidence) for evidence in evidence_rows) < cutoff


def _is_top_tier_developer(developer_name: Any, session: Session) -> bool:
    if not developer_name:
        return False
    return (
        session.execute(
            select(DeveloperRegistry.id).where(
                DeveloperRegistry.canonical_name == str(developer_name),
                DeveloperRegistry.is_top_tier.is_(True),
            )
        ).scalar_one_or_none()
        is not None
    )


def _effective_date(evidence: Evidence) -> date:
    return evidence.evidence_date or evidence.collected_at.date()


def _months_ago(months: int) -> date:
    today = date.today()
    target_month = today.month - months
    target_year = today.year
    while target_month <= 0:
        target_month += 12
        target_year -= 1
    return date(target_year, target_month, 1)
