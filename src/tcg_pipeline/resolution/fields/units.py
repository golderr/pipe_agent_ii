from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import Evidence, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldObservation,
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
)

SPLIT_SOURCE_ALLOWLIST = {
    "pipedream",
    "lahd_affordable",
    "sm_dev_tracking",
    "news_article",
}


def resolve_units(
    evidence_rows: list[Evidence],
    project: Project,
    field_name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = iter_field_observations(evidence_rows, field_name)
    if not observations:
        candidate = build_resolution(
            field_name,
            getattr(project, field_name),
            confidence=StatusConfidence.LOW,
            rule_applied="no_explicit_units_evidence",
        )
        return apply_override(field_name, candidate, overrides, transform_value=_coerce_int)

    value = _coerce_int(observations[0].value)
    candidate = build_resolution(
        field_name,
        value,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_wins",
    )
    return apply_override(field_name, candidate, overrides, transform_value=_coerce_int)


def resolve_unit_split(
    evidence_rows: list[Evidence],
    project: Project,
    field_name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = [
        observation
        for observation in iter_field_observations(evidence_rows, field_name)
        if _is_split_source_allowed(observation)
    ]
    if not observations:
        candidate = build_resolution(
            field_name,
            getattr(project, field_name),
            confidence=StatusConfidence.LOW,
            rule_applied="no_allowed_split_evidence",
            metadata={"allowed_sources": sorted(SPLIT_SOURCE_ALLOWLIST)},
        )
        return apply_override(field_name, candidate, overrides, transform_value=_coerce_int)

    value = _coerce_int(observations[0].value)
    candidate = build_resolution(
        field_name,
        value,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_allowed_split_source_wins",
        metadata={
            "source_type": observations[0].evidence.source_type,
            "allowed_sources": sorted(SPLIT_SOURCE_ALLOWLIST),
        },
    )
    return apply_override(field_name, candidate, overrides, transform_value=_coerce_int)


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_split_source_allowed(observation: FieldObservation) -> bool:
    return observation.evidence.source_type in SPLIT_SOURCE_ALLOWLIST
