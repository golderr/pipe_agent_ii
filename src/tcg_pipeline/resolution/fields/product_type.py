from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import Evidence, ProductType, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    resolve_override,
)


def resolve_product_type(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    override = resolve_override("product_type", overrides)
    if override is not None:
        override.value = _coerce_product_type(override.value) or ProductType.UNKNOWN
        return override

    observations = iter_field_observations(evidence_rows, "product_type")
    if not observations:
        return build_resolution(
            "product_type",
            project.product_type or ProductType.UNKNOWN,
            confidence=StatusConfidence.LOW,
            rule_applied="no_product_type_evidence_keep_current",
        )

    product_type = _coerce_product_type(observations[0].value) or ProductType.UNKNOWN
    return build_resolution(
        "product_type",
        product_type,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_wins",
    )


def _coerce_product_type(value: Any) -> ProductType | None:
    if isinstance(value, ProductType):
        return value
    if value is None:
        return None
    try:
        return ProductType(str(value))
    except ValueError:
        return None
