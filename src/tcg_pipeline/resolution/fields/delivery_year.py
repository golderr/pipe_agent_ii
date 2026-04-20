from __future__ import annotations

import math
from datetime import date
from typing import Any

from tcg_pipeline.db.models import Evidence, PipelineStatus, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    parse_date_value,
    resolve_override,
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
    override = resolve_override("date_delivery", overrides)
    if override is not None:
        override.value = parse_date_value(override.value)
        override.metadata.setdefault("provenance", "researcher_override")
        return override

    observations = iter_field_observations(evidence_rows, "date_delivery")
    if observations:
        resolved_date = parse_date_value(observations[0].value)
        if resolved_date is not None and _should_keep_explicit_delivery_date(
            resolved_date,
            observations[0].effective_date,
            status=resolved_status,
        ):
            provenance = _provenance_for_source_type(observations[0].evidence.source_type)
            return build_resolution(
                "date_delivery",
                resolved_date,
                confidence=infer_confidence(observations),
                observations=observations[:1],
                rule_applied="explicit_delivery_date",
                metadata={"provenance": provenance},
            )

    estimated_date = _estimate_delivery_date(
        status=resolved_status,
        total_units=resolved_total_units,
    )
    return build_resolution(
        "date_delivery",
        estimated_date,
        confidence=StatusConfidence.LOW,
        rule_applied="estimated_calc",
        metadata={"provenance": "estimated_calc"},
    )


def _should_keep_explicit_delivery_date(
    resolved_date: date,
    evidence_date: date,
    *,
    status: PipelineStatus,
) -> bool:
    if status == PipelineStatus.UNDER_CONSTRUCTION and resolved_date >= date.today():
        return True
    return evidence_date >= _months_ago(6)


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


def _months_ago(month_count: int) -> date:
    today = date.today()
    target_month = today.month - month_count
    target_year = today.year
    while target_month <= 0:
        target_month += 12
        target_year -= 1
    return date(target_year, target_month, 1)


def _provenance_for_source_type(source_type: str) -> str:
    if source_type == "pipedream":
        return "explicit_tcg"
    if source_type == "costar":
        return "explicit_costar"
    if source_type == "news_article":
        return "explicit_news"
    return "explicit_government"
