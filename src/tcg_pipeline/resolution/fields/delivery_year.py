from __future__ import annotations

import math
from datetime import date
from typing import Any

from tcg_pipeline.db.models import Evidence, PipelineStatus, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    parse_date_value,
)

BASE_YEARS = {
    PipelineStatus.UNDER_CONSTRUCTION: 2.0,
    PipelineStatus.APPROVED: 3.0,
    PipelineStatus.PENDING: 4.5,
    PipelineStatus.PROPOSED: 5.5,
    PipelineStatus.CONCEPTUAL: 7.0,
}


def resolve_delivery_year(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    resolved_status: PipelineStatus,
    resolved_total_units: int | None,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = iter_field_observations(evidence_rows, "date_delivery")
    if observations:
        resolved_date = parse_date_value(observations[0].value)
        if resolved_date is not None:
            provenance = _provenance_for_source_type(observations[0].evidence.source_type)
            candidate = build_resolution(
                "date_delivery",
                resolved_date,
                confidence=infer_confidence(observations),
                observations=observations[:1],
                rule_applied="explicit_delivery_date",
                metadata={
                    "provenance": provenance,
                    "delivery_date_type": provenance,
                    "source_type": observations[0].evidence.source_type,
                    "description": (
                        f"Explicit delivery date from {observations[0].evidence.source_type} "
                        f"evidence dated {observations[0].effective_date.isoformat()}."
                    ),
                },
            )
            return _apply_delivery_override(candidate, overrides)

    if project.date_delivery is not None:
        candidate = build_resolution(
            "date_delivery",
            project.date_delivery,
            confidence=StatusConfidence.LOW,
            rule_applied="no_explicit_delivery_evidence_keep_current",
            metadata={
                "provenance": project.delivery_year_provenance,
                "delivery_date_type": project.delivery_year_provenance,
                "description": (
                    "Retained existing project delivery date because no explicit delivery "
                    "evidence was available."
                ),
            },
        )
        return _apply_delivery_override(candidate, overrides)

    estimated_date = _estimate_delivery_date(
        status=resolved_status,
        total_units=resolved_total_units,
    )
    candidate = build_resolution(
        "date_delivery",
        estimated_date,
        confidence=StatusConfidence.LOW,
        rule_applied="estimated_calc",
        metadata={
            "provenance": "estimated_calc",
            "delivery_date_type": "estimated_calc",
            "description": (
                "Estimated delivery date derived from resolved status and unit-count "
                "size adjustment."
            ),
            "estimate_inputs": {
                "status": resolved_status.value,
                "total_units": resolved_total_units,
            },
        },
    )
    return _apply_delivery_override(candidate, overrides)


def _apply_delivery_override(
    candidate: FieldResolution,
    overrides: dict[str, Any] | None,
) -> FieldResolution:
    resolution = apply_override(
        "date_delivery",
        candidate,
        overrides,
        transform_value=parse_date_value,
    )
    if resolution.rule_applied.startswith("researcher_override"):
        resolution.metadata = dict(resolution.metadata)
        resolution.metadata["provenance"] = "researcher_override"
        resolution.metadata["delivery_date_type"] = "researcher_override"
        resolution.metadata.setdefault(
            "description",
            "Delivery date is currently set by a researcher override.",
        )
    return resolution


def _estimate_delivery_date(
    *,
    status: PipelineStatus,
    total_units: int | None,
) -> date | None:
    if status not in BASE_YEARS:
        return None

    size_adjustment = 0.0
    if total_units is not None:
        if total_units < 200:
            size_adjustment = -0.5
        elif total_units <= 500:
            size_adjustment = 0.0
        elif total_units <= 1000:
            size_adjustment = 1.0
        else:
            size_adjustment = 1.5

    estimated_year = math.ceil(date.today().year + BASE_YEARS[status] + size_adjustment)
    return date(estimated_year, 7, 1)


def _provenance_for_source_type(source_type: str) -> str:
    if source_type == "pipedream":
        return "explicit_tcg"
    if source_type == "costar":
        return "explicit_costar"
    if source_type == "news_article":
        return "explicit_news"
    return "explicit_government"
