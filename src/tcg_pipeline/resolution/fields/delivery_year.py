from __future__ import annotations

import math
from datetime import date, timedelta
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
# EVIDENCE_LAYER_DECISIONS.md §21f defines this as "within the last 6 months".
RECENT_NEWS_DELIVERY_DAYS = 180
NEWS_ARTICLE_SOURCE_TYPE = "news_article"
COSTAR_SOURCE_TYPES = {"costar"}
TCG_SOURCE_TYPES = {"pipedream"}


def resolve_delivery_year(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    resolved_status: PipelineStatus,
    resolved_total_units: int | None,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = iter_field_observations(evidence_rows, "date_delivery")
    explicit_observation = _select_explicit_delivery_observation(
        observations,
        resolved_status=resolved_status,
    )
    if explicit_observation is not None:
        resolved_date = parse_date_value(explicit_observation.value)
        if resolved_date is not None:
            provenance = _provenance_for_source_type(explicit_observation.evidence.source_type)
            candidate = build_resolution(
                "date_delivery",
                resolved_date,
                confidence=infer_confidence(observations),
                observations=[explicit_observation],
                rule_applied="explicit_delivery_date",
                metadata={
                    "provenance": provenance,
                    "delivery_date_type": provenance,
                    "source_type": explicit_observation.evidence.source_type,
                    "description": (
                        f"Explicit delivery date from {explicit_observation.evidence.source_type} "
                        f"evidence dated {explicit_observation.effective_date.isoformat()}."
                    ),
                },
            )
            return _apply_delivery_override(candidate, overrides)

    if resolved_status == PipelineStatus.COMPLETE:
        prior_explicit_date = _current_explicit_project_delivery(project)
        if prior_explicit_date is not None:
            candidate = build_resolution(
                "date_delivery",
                prior_explicit_date,
                confidence=StatusConfidence.LOW,
                rule_applied="complete_reject_future_delivery_keep_current",
                metadata={
                    "provenance": project.delivery_year_provenance,
                    "delivery_date_type": project.delivery_year_provenance,
                    "description": (
                        "Ignored future delivery-date evidence because the resolved status "
                        "is Complete; retained the existing non-future explicit project date."
                    ),
                },
            )
            return _apply_delivery_override(candidate, overrides)

    if project.date_delivery is not None and not (
        resolved_status == PipelineStatus.COMPLETE and project.date_delivery > date.today()
    ):
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
    if source_type in TCG_SOURCE_TYPES:
        return "explicit_tcg"
    if source_type in COSTAR_SOURCE_TYPES:
        return "explicit_costar"
    if source_type == NEWS_ARTICLE_SOURCE_TYPE:
        return "explicit_news"
    return "explicit_government"


def _select_explicit_delivery_observation(
    observations,
    *,
    resolved_status: PipelineStatus,
):
    if not observations:
        return None
    if resolved_status != PipelineStatus.COMPLETE:
        return _prefer_recent_news_over_costar(observations)

    today = date.today()
    eligible_observations = []
    for observation in observations:
        resolved_date = parse_date_value(observation.value)
        if resolved_date is None:
            continue
        if resolved_date <= today:
            eligible_observations.append(observation)
    if not eligible_observations:
        return None
    return _prefer_recent_news_over_costar(eligible_observations)


def _prefer_recent_news_over_costar(observations):
    winner = observations[0]
    if winner.evidence.source_type not in COSTAR_SOURCE_TYPES:
        return winner
    recent_news = [
        observation for observation in observations if _is_recent_news_article(observation)
    ]
    return recent_news[0] if recent_news else winner


def _is_recent_news_article(observation) -> bool:
    if observation.evidence.source_type != NEWS_ARTICLE_SOURCE_TYPE:
        return False
    evidence_date = observation.evidence.evidence_date
    if evidence_date is None:
        return False
    return evidence_date >= date.today() - timedelta(days=RECENT_NEWS_DELIVERY_DAYS)


def _current_explicit_project_delivery(project: Project) -> date | None:
    if project.date_delivery is None:
        return None
    if project.date_delivery > date.today():
        return None
    provenance = str(project.delivery_year_provenance or "")
    if not provenance.startswith("explicit_"):
        return None
    return project.date_delivery
