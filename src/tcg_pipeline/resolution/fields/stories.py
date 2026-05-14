from __future__ import annotations

from typing import Any

from tcg_pipeline.db.models import Evidence, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    sort_observations,
)

# LADBS is intentionally not promoted here. Deprecated LADBS feeds may still emit
# mapped_fields["stories"] when of_stories is present, but active-feed
# height-to-stories semantics remain deferred in the cycle 1 prep plan, Section 6.
STORIES_SOURCE_PRIORITY = {
    "pipedream": 0,
    "costar": 1,
    "news_article": 2,
}


def resolve_stories(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = [
        observation
        for observation in iter_field_observations(evidence_rows, "stories")
        if _coerce_int(observation.value) is not None
    ]
    observations = sort_observations(observations, source_priority=STORIES_SOURCE_PRIORITY)
    if not observations:
        candidate = build_resolution(
            "stories",
            project.stories,
            confidence=StatusConfidence.LOW,
            rule_applied="no_explicit_stories_evidence",
        )
        return apply_override(
            "stories",
            candidate,
            overrides,
            transform_value=_coerce_int,
            source_priority=STORIES_SOURCE_PRIORITY,
        )

    value = _coerce_int(observations[0].value)
    candidate = build_resolution(
        "stories",
        value,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied="most_recent_wins",
    )
    return apply_override(
        "stories",
        candidate,
        overrides,
        transform_value=_coerce_int,
        source_priority=STORIES_SOURCE_PRIORITY,
    )


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
