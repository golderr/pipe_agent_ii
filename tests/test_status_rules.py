from __future__ import annotations

from datetime import date

from tcg_pipeline.db.models import PipelineStatus, Priority
from tcg_pipeline.status_rules import build_status_suggestion, get_status_evidence_rule


def test_get_status_evidence_rule_returns_building_permit_rule() -> None:
    rule = get_status_evidence_rule("building_permit_issued")

    assert rule is not None
    assert rule.suggested_status == PipelineStatus.APPROVED
    assert rule.priority == Priority.HIGH
    assert rule.proof_level == "supporting"


def test_build_status_suggestion_uses_central_rule() -> None:
    suggestion = build_status_suggestion(
        current_status=PipelineStatus.PENDING,
        evidence_type="building_permit_issued",
        evidence_date=date(2013, 1, 2),
    )

    assert suggestion is not None
    assert suggestion.current_status == PipelineStatus.PENDING
    assert suggestion.suggested_status == PipelineStatus.APPROVED
    assert suggestion.priority == Priority.HIGH
    assert suggestion.rule_code == "building_permit_issued"
    assert suggestion.proof_level == "supporting"


def test_build_status_suggestion_ignores_non_advancing_signal() -> None:
    suggestion = build_status_suggestion(
        current_status=PipelineStatus.UNDER_CONSTRUCTION,
        evidence_type="building_permit_issued",
        evidence_date=date(2013, 1, 2),
    )

    assert suggestion is None


def test_build_status_suggestion_supports_unmatched_candidate() -> None:
    suggestion = build_status_suggestion(
        current_status=None,
        evidence_type="building_permit_issued",
        evidence_date=date(2013, 1, 2),
    )

    assert suggestion is not None
    assert suggestion.current_status is None
    assert suggestion.suggested_status == PipelineStatus.APPROVED
    assert suggestion.rule_code == "building_permit_issued"
