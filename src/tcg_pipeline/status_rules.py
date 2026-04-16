from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tcg_pipeline.db.models import PipelineStatus, Priority

STATUS_ORDER = {
    PipelineStatus.CONCEPTUAL: 0,
    PipelineStatus.PROPOSED: 1,
    PipelineStatus.PENDING: 2,
    PipelineStatus.APPROVED: 3,
    PipelineStatus.UNDER_CONSTRUCTION: 4,
    PipelineStatus.PRE_LEASING_PRE_SELLING: 5,
    PipelineStatus.COMPLETE: 6,
}


@dataclass(frozen=True, slots=True)
class StatusEvidenceRule:
    evidence_type: str
    suggested_status: PipelineStatus
    priority: Priority
    proof_level: str
    reason: str


@dataclass(frozen=True, slots=True)
class StatusSuggestion:
    current_status: PipelineStatus | None
    suggested_status: PipelineStatus
    evidence_type: str
    evidence_date: date | None
    reason: str
    priority: Priority
    rule_code: str
    proof_level: str


STATUS_EVIDENCE_RULES: dict[str, StatusEvidenceRule] = {
    "building_permit_issued": StatusEvidenceRule(
        evidence_type="building_permit_issued",
        suggested_status=PipelineStatus.APPROVED,
        priority=Priority.HIGH,
        proof_level="supporting",
        reason=(
            "Building permit issued. Per TCG status definitions, permit issuance supports "
            "Approved but does not prove Under Construction."
        ),
    ),
}


def get_status_evidence_rule(evidence_type: str | None) -> StatusEvidenceRule | None:
    if not evidence_type:
        return None
    return STATUS_EVIDENCE_RULES.get(evidence_type)


def build_status_suggestion(
    *,
    current_status: PipelineStatus | None,
    evidence_type: str | None,
    evidence_date: date | None,
    reason_override: str | None = None,
) -> StatusSuggestion | None:
    rule = get_status_evidence_rule(evidence_type)
    if rule is None:
        return None
    if not _should_emit_status_suggestion(current_status, rule.suggested_status):
        return None

    return StatusSuggestion(
        current_status=current_status,
        suggested_status=rule.suggested_status,
        evidence_type=rule.evidence_type,
        evidence_date=evidence_date,
        reason=reason_override or rule.reason,
        priority=rule.priority,
        rule_code=rule.evidence_type,
        proof_level=rule.proof_level,
    )


def _should_emit_status_suggestion(
    current_status: PipelineStatus | None,
    suggested_status: PipelineStatus,
) -> bool:
    if current_status is None:
        return True
    if current_status == suggested_status:
        return False

    current_rank = STATUS_ORDER.get(current_status)
    suggested_rank = STATUS_ORDER.get(suggested_status)
    if current_rank is not None and suggested_rank is not None:
        return suggested_rank > current_rank

    return True
