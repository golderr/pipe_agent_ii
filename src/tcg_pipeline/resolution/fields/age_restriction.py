from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import AgeRestriction, Evidence, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    resolve_override,
)


def resolve_age_restriction(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    override = resolve_override("age_restriction", overrides)
    if override is not None:
        override.value = _coerce_age_restriction(override.value) or AgeRestriction.UNKNOWN
        return override

    observations = iter_field_observations(evidence_rows, "age_restriction")
    if not observations:
        return build_resolution(
            "age_restriction",
            AgeRestriction.UNKNOWN,
            confidence=StatusConfidence.LOW,
            rule_applied="no_age_restriction_evidence",
        )

    age_restriction = _coerce_age_restriction(observations[0].value) or AgeRestriction.UNKNOWN
    return build_resolution(
        "age_restriction",
        age_restriction,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_explicit_wins",
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
