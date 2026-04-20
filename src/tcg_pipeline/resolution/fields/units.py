from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import Evidence, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    resolve_override,
)


def resolve_units(
    evidence_rows: list[Evidence],
    project: Project,
    field_name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    override = resolve_override(field_name, overrides)
    if override is not None:
        override.value = _coerce_int(override.value)
        return override

    observations = iter_field_observations(evidence_rows, field_name)
    if not observations:
        return build_resolution(
            field_name,
            getattr(project, field_name),
            confidence=StatusConfidence.LOW,
            rule_applied="no_explicit_units_evidence",
        )

    value = _coerce_int(observations[0].value)
    return build_resolution(
        field_name,
        value,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_wins",
    )


def resolve_unit_split(
    evidence_rows: list[Evidence],
    project: Project,
    field_name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    return resolve_units(evidence_rows, project, field_name, overrides=overrides)


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
