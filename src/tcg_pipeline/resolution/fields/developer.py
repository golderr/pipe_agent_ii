from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from tcg_pipeline.db.models import Evidence, Project, StatusConfidence
from tcg_pipeline.developer.registry import canonicalize_developer_name
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
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
    session: Session | None = None,
    persist_registry: bool = False,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    observations = iter_field_observations(evidence_rows, "developer")
    observations = sort_observations(
        observations,
        source_priority=DEVELOPER_SOURCE_PRIORITY,
    )
    if not observations:
        candidate = build_resolution(
            "developer",
            project.developer,
            confidence=StatusConfidence.LOW,
            rule_applied="no_developer_evidence_keep_current",
        )
        return apply_override("developer", candidate, overrides, transform_value=_coerce_text)

    raw_developer_name = _coerce_text(observations[0].value)
    developer_name = raw_developer_name
    metadata = {"source_type": observations[0].evidence.source_type}
    rule_applied = "most_recent_wins"
    if session is not None and developer_name is not None:
        canonicalization = canonicalize_developer_name(
            session,
            developer_name,
            persist=persist_registry,
        )
        if canonicalization.requires_review:
            if _coerce_text(project.developer) == _coerce_text(canonicalization.canonical_name):
                developer_name = canonicalization.canonical_name
            else:
                developer_name = raw_developer_name
            rule_applied = "most_recent_wins_canonicalization_review_required"
        else:
            developer_name = canonicalization.canonical_name
        metadata.update(
            {
                "raw_value": raw_developer_name,
                "canonical_name": canonicalization.canonical_name,
                "match_type": canonicalization.match_type,
                "score": canonicalization.score,
                "requires_review": canonicalization.requires_review,
                "registry_created": canonicalization.registry_created,
                "registry_merged": canonicalization.registry_merged,
                "alias_created": canonicalization.alias_created,
                "is_top_tier": canonicalization.is_top_tier,
            }
        )
        if (
            canonicalization.match_type != "exact_canonical"
            and not canonicalization.requires_review
        ):
            rule_applied = "most_recent_wins_canonicalized"

    candidate = build_resolution(
        "developer",
        developer_name,
        confidence=infer_confidence(observations),
        observations=observations[:1],
        rule_applied=rule_applied,
        metadata=metadata,
    )
    return apply_override(
        "developer",
        candidate,
        overrides,
        transform_value=_coerce_text,
        source_priority=DEVELOPER_SOURCE_PRIORITY,
    )


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
