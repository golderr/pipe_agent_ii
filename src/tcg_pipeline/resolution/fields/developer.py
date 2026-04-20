from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import Evidence, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    resolve_override,
    sort_observations,
)

DEVELOPER_SOURCE_PRIORITY = {
    "pipedream": 0,
    "news_article": 1,
    "developer_website": 2,
    "costar": 3,
    "ladbs_permit": 4,
    "ladbs_inspection": 4,
    "ladbs_cofo": 4,
}


def resolve_developer(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    override = resolve_override("developer", overrides)
    if override is not None:
        override.value = _coerce_text(override.value)
        return override

    observations = iter_field_observations(evidence_rows, "developer")
    observations = sort_observations(
        observations,
        source_priority=DEVELOPER_SOURCE_PRIORITY,
    )
    if not observations:
        return build_resolution(
            "developer",
            None,
            confidence=StatusConfidence.LOW,
            rule_applied="no_developer_evidence",
        )

    developer_name = _coerce_text(observations[0].value)
    return build_resolution(
        "developer",
        developer_name,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_wins",
        metadata={"source_type": observations[0].evidence.source_type},
    )


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
