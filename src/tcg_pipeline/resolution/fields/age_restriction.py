from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import AgeRestriction, Evidence, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
)


def resolve_age_restriction(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = iter_field_observations(evidence_rows, "age_restriction")
    if not observations:
        candidate = build_resolution(
            "age_restriction",
            project.age_restriction or AgeRestriction.UNKNOWN,
            confidence=StatusConfidence.LOW,
            rule_applied="no_age_restriction_evidence_keep_current",
        )
        return apply_override(
            "age_restriction",
            candidate,
            overrides,
            transform_value=lambda value: (
                _coerce_age_restriction(value) or AgeRestriction.UNKNOWN
            ),
        )

    age_restriction = _coerce_age_restriction(observations[0].value) or AgeRestriction.UNKNOWN
    candidate = build_resolution(
        "age_restriction",
        age_restriction,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_explicit_wins",
    )
    return apply_override(
        "age_restriction",
        candidate,
        overrides,
        transform_value=lambda value: _coerce_age_restriction(value) or AgeRestriction.UNKNOWN,
    )


def _coerce_age_restriction(value: Any) -> AgeRestriction | None:
    if isinstance(value, AgeRestriction):
        return value
    if value is None:
        return None
    try:
        return AgeRestriction(str(value))
    except ValueError:
        return None
