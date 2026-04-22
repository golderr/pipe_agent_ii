from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import Evidence, ProductType, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
)


def resolve_product_type(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = iter_field_observations(evidence_rows, "product_type")
    if not observations:
        candidate = build_resolution(
            "product_type",
            project.product_type or ProductType.UNKNOWN,
            confidence=StatusConfidence.LOW,
            rule_applied="no_product_type_evidence_keep_current",
        )
        return apply_override(
            "product_type",
            candidate,
            overrides,
            transform_value=lambda value: _coerce_product_type(value) or ProductType.UNKNOWN,
        )

    product_type = _coerce_product_type(observations[0].value) or ProductType.UNKNOWN
    candidate = build_resolution(
        "product_type",
        product_type,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_wins",
    )
    return apply_override(
        "product_type",
        candidate,
        overrides,
        transform_value=lambda value: _coerce_product_type(value) or ProductType.UNKNOWN,
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
