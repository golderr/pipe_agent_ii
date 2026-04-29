from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from tcg_pipeline.db.models import (
    Evidence,
    Priority,
    Project,
    ResearcherOverride,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
)
from tcg_pipeline.developer.registry import (
    canonicalize_developer_name,
    normalize_developer_name,
)
from tcg_pipeline.resolution.fields import FieldResolution, normalize_comparable
from tcg_pipeline.review.decision_cards import (
    proposed_values_match,
    upsert_decision_card_review_item,
)

ACTIVE_REVIEW_STATES = {"open", "staged"}
REVIEW_PROTECTED_MODES = {"review_protected", "until_newer_evidence", "sticky", None}
CONTRADICTION_DETECTION_ACTOR = "contradiction_detection"
NEWS_SOURCE_TYPES = {"news_article", "news", "article", "bizjournals"}
UNIT_FIELDS = {"total_units", "affordable_units", "market_rate_units"}
LARGE_UNIT_DELTA = 50
SMALL_UNIT_DELTA = 5
DELIVERY_DATE_DELTA_DAYS = 30
RECENT_ARTICLE_DAYS = 180
CONFIDENT_DEVELOPER_MATCH_TYPES = {"exact_canonical", "exact_alias", "fuzzy_auto"}


@dataclass(slots=True)
class ContradictionDetectionResult:
    created_items: list[ReviewItem] = field(default_factory=list)
    updated_items: list[ReviewItem] = field(default_factory=list)
    invalidated_items: list[ReviewItem] = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return len(self.created_items)

    @property
    def updated_count(self) -> int:
        return len(self.updated_items)

    @property
    def invalidated_count(self) -> int:
        return len(self.invalidated_items)

    def extend(self, other: ContradictionDetectionResult) -> None:
        self.created_items.extend(other.created_items)
        self.updated_items.extend(other.updated_items)
        self.invalidated_items.extend(other.invalidated_items)


def detect_contradictions(
    session: Session,
    project_ids: Iterable[uuid.UUID],
) -> ContradictionDetectionResult:
    """Detect override contradictions for projects using a dry-run resolution pass."""

    # Circular import: resolve_project calls back into this module after apply=True runs.
    from tcg_pipeline.resolution import resolve_project

    result = ContradictionDetectionResult()
    for project_id in sorted({uuid.UUID(str(value)) for value in project_ids}, key=str):
        project = session.get(Project, project_id)
        if project is None:
            continue
        resolution_result = resolve_project(
            project.id,
            session,
            apply=False,
            write_resolution_log=False,
        )
        result.extend(
            detect_project_contradictions(
                session,
                project=project,
                field_resolutions=resolution_result.field_resolutions,
            )
        )
    return result


def detect_project_contradictions(
    session: Session,
    *,
    project: Project,
    field_resolutions: Mapping[str, FieldResolution],
    skip_review_item_ids: set[uuid.UUID] | None = None,
) -> ContradictionDetectionResult:
    active_override_ids = _active_override_ids_by_field(session, project)
    existing_items = _existing_override_contradiction_items_by_field(session, project)
    skip_review_item_ids = skip_review_item_ids or set()
    result = ContradictionDetectionResult()
    contradicted_fields: set[str] = set()

    for field_name, resolution in field_resolutions.items():
        if not is_override_contradiction(session, field_name, resolution):
            continue

        contradicted_fields.add(field_name)
        priority = contradiction_priority(field_name, resolution)
        payload = contradiction_payload(
            session,
            project=project,
            field_name=field_name,
            resolution=resolution,
        )
        existing_item = existing_items.get(field_name)
        if existing_item is not None and existing_item.id in skip_review_item_ids:
            continue

        item, created = upsert_decision_card_review_item(
            session,
            project_id=project.id,
            item_type=ReviewItemType.OVERRIDE_CONTRADICTION,
            field_name=field_name,
            priority=priority,
            payload=payload,
            proposed_value=resolution.metadata.get("candidate_value"),
            winning_evidence_id=_winning_candidate_evidence_id(session, resolution),
            contradicted_override_id=active_override_ids.get(field_name),
            contradiction_priority=priority.value,
        )
        if created:
            result.created_items.append(item)
            if (
                existing_item is not None
                and existing_item.state == "invalidated"
                and not proposed_values_match(existing_item.payload, payload.get("proposed_value"))
            ):
                result.invalidated_items.append(existing_item)
        else:
            result.updated_items.append(item)

    for field_name, existing_item in existing_items.items():
        if field_name in contradicted_fields or existing_item.id in skip_review_item_ids:
            continue
        _invalidate_review_item(existing_item)
        result.invalidated_items.append(existing_item)

    return result


def is_override_contradiction(
    session: Session,
    field_name: str,
    resolution: FieldResolution,
) -> bool:
    if not resolution.rule_applied.startswith("researcher_override"):
        return False
    if resolution.metadata.get("mode") not in REVIEW_PROTECTED_MODES:
        return False
    if not _candidate_can_reopen_review(resolution):
        return False
    if not resolution.metadata.get("candidate_evidence_ids"):
        return False
    return values_contradict(
        field_name,
        resolution.value,
        resolution.metadata.get("candidate_value"),
        resolution,
        session=session,
    )


def _candidate_can_reopen_review(resolution: FieldResolution) -> bool:
    if resolution.metadata.get("candidate_is_newer_than_baseline"):
        return True
    # Baseline-less overrides are legacy rows. New C.d/C.h write paths should
    # capture baselines; legacy rows must still surface divergent evidence once.
    return not isinstance(resolution.metadata.get("baseline"), Mapping)


def values_contradict(
    field_name: str,
    override_value: Any,
    candidate_value: Any,
    resolution: FieldResolution | None = None,
    *,
    session: Session | None = None,
) -> bool:
    override_normalized = normalize_comparable(override_value)
    candidate_normalized = normalize_comparable(candidate_value)
    if override_normalized == candidate_normalized:
        return False
    if field_name in UNIT_FIELDS:
        try:
            return abs(int(override_normalized) - int(candidate_normalized)) > SMALL_UNIT_DELTA
        except (TypeError, ValueError):
            return True
    if field_name == "date_delivery":
        override_date = _date_from_comparable(override_normalized)
        candidate_date = _date_from_comparable(candidate_normalized)
        if override_date is None or candidate_date is None:
            return True
        if abs((override_date - candidate_date).days) > DELIVERY_DATE_DELTA_DAYS:
            return True
        return _candidate_is_recent_article(resolution)
    if field_name == "developer":
        return _developers_contradict(session, override_normalized, candidate_normalized)
    return True


def _developers_contradict(
    session: Session | None,
    override_value: Any,
    candidate_value: Any,
) -> bool:
    override_text = _coerce_text(override_value)
    candidate_text = _coerce_text(candidate_value)
    if override_text is None or candidate_text is None:
        return override_text != candidate_text

    override_normalized = normalize_developer_name(override_text)
    candidate_normalized = normalize_developer_name(candidate_text)
    if override_normalized == candidate_normalized:
        return False
    # Production contradiction detection passes a session so registry aliases can
    # suppress known-equivalent developer names. Session-less callers get only
    # normalization-level comparison.
    if session is None:
        return True

    override_canonical = canonicalize_developer_name(session, override_text, persist=False)
    candidate_canonical = canonicalize_developer_name(session, candidate_text, persist=False)
    if (
        override_canonical.match_type in CONFIDENT_DEVELOPER_MATCH_TYPES
        and candidate_canonical.match_type in CONFIDENT_DEVELOPER_MATCH_TYPES
        and override_canonical.canonical_developer_id is not None
        and override_canonical.canonical_developer_id
        == candidate_canonical.canonical_developer_id
    ):
        return False
    return True


def contradiction_priority(
    field_name: str,
    resolution: FieldResolution,
) -> Priority:
    candidate_frontier = resolution.metadata.get("candidate_evidence_frontier")
    if isinstance(candidate_frontier, dict) and candidate_frontier.get("source_tier") == 1:
        return Priority.HIGH
    if field_name in UNIT_FIELDS:
        try:
            delta = abs(
                int(normalize_comparable(resolution.value))
                - int(resolution.metadata.get("candidate_value"))
            )
        except (TypeError, ValueError):
            delta = 0
        if delta > LARGE_UNIT_DELTA:
            return Priority.HIGH
    if field_name == "pipeline_status":
        return Priority.HIGH
    return Priority.MEDIUM


def contradiction_payload(
    session: Session,
    *,
    project: Project,
    field_name: str,
    resolution: FieldResolution,
) -> dict[str, Any]:
    evidence_ids = _contradiction_evidence_ids(
        session,
        project=project,
        field_name=field_name,
        resolution=resolution,
    )
    return {
        "origin": "override_contradiction_detection",
        "field_name": field_name,
        "current_override": {
            "value": normalize_comparable(resolution.value),
            "set_by": resolution.metadata.get("set_by"),
            "set_at": resolution.metadata.get("set_at"),
            "note": resolution.metadata.get("note"),
            "mode": resolution.metadata.get("mode"),
            "baseline": resolution.metadata.get("baseline"),
        },
        "proposed_value": resolution.metadata.get("candidate_value"),
        "evidence_ids": evidence_ids,
        "candidate": {
            "value": resolution.metadata.get("candidate_value"),
            "rule_applied": resolution.metadata.get("candidate_rule_applied"),
            "confidence": resolution.metadata.get("candidate_confidence"),
            "evidence_ids": resolution.metadata.get("candidate_evidence_ids") or [],
            "evidence_date": resolution.metadata.get("candidate_evidence_date"),
            "evidence_frontier": _json_safe(
                resolution.metadata.get("candidate_evidence_frontier")
            ),
        },
        "message": f"Newer evidence contradicts the manually edited {field_name} value.",
    }


def _active_override_ids_by_field(
    session: Session,
    project: Project,
) -> dict[str, uuid.UUID]:
    rows = session.execute(
        select(ResearcherOverride.field_name, ResearcherOverride.id).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.cleared_at.is_(None),
        )
    ).all()
    return {field_name: override_id for field_name, override_id in rows}


def _existing_override_contradiction_items_by_field(
    session: Session,
    project: Project,
) -> dict[str, ReviewItem]:
    rows = session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
            ReviewItem.state.in_(ACTIVE_REVIEW_STATES),
        )
    ).scalars().all()
    items_by_field: dict[str, ReviewItem] = {}
    for item in rows:
        payload = item.payload if isinstance(item.payload, dict) else {}
        field_name = item.field_name or payload.get("field_name")
        if isinstance(field_name, str):
            items_by_field[field_name] = item
    return items_by_field


def _invalidate_review_item(review_item: ReviewItem) -> None:
    now = datetime.now(UTC)
    payload = dict(review_item.payload) if isinstance(review_item.payload, dict) else {}
    payload["invalidated_at"] = now.isoformat()
    payload["invalidated_reason"] = "override_contradiction_resolved"
    review_item.payload = payload
    review_item.state = "invalidated"
    review_item.status = ReviewItemStatus.OPEN
    review_item.resolved_at = now
    review_item.resolved_by = CONTRADICTION_DETECTION_ACTOR
    session = object_session(review_item)
    for decision in list(review_item.decisions):
        # TODO(C.j): expose dropped staged decisions as user-facing queue notifications.
        if decision.state == "staged" and session is not None:
            session.delete(decision)


def _candidate_is_recent_article(resolution: FieldResolution | None) -> bool:
    if resolution is None:
        return False
    frontier = resolution.metadata.get("candidate_evidence_frontier")
    if not isinstance(frontier, dict):
        return False
    source_type = str(frontier.get("source_type") or "").lower()
    if source_type not in NEWS_SOURCE_TYPES and not any(
        token in source_type for token in NEWS_SOURCE_TYPES
    ):
        return False
    candidate_date = _date_from_comparable(
        resolution.metadata.get("candidate_evidence_date")
    )
    if candidate_date is None:
        return False
    return candidate_date >= date.today() - timedelta(days=RECENT_ARTICLE_DAYS)


def _date_from_comparable(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(inner_value) for key, inner_value in value.items()}
    if isinstance(value, list):
        return [_json_safe(inner_value) for inner_value in value]
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _contradiction_evidence_ids(
    session: Session,
    *,
    project: Project,
    field_name: str,
    resolution: FieldResolution,
) -> list[str]:
    evidence_ids: list[str] = []
    candidate_ids = resolution.metadata.get("candidate_evidence_ids") or []
    evidence_ids.extend(str(evidence_id) for evidence_id in candidate_ids if evidence_id)
    evidence_ids.extend(
        str(evidence_id)
        for evidence_id in _supporting_evidence_ids(
            session,
            project=project,
            field_name=field_name,
            override_value=resolution.value,
        )
    )
    return _dedupe_text(evidence_ids)


def _supporting_evidence_ids(
    session: Session,
    *,
    project: Project,
    field_name: str,
    override_value: Any,
) -> list[uuid.UUID]:
    rows = session.execute(
        select(Evidence).where(
            Evidence.project_id == project.id,
            Evidence.extracted_fields.isnot(None),
            Evidence.extracted_fields.op("?")(field_name),
        )
    ).scalars().all()
    supporting: list[uuid.UUID] = []
    for evidence in rows:
        field_payload = (evidence.extracted_fields or {}).get(field_name)
        if not isinstance(field_payload, Mapping) or "value" not in field_payload:
            continue
        # Evidence values are expected to be normalized field values, not raw source
        # signal tokens. For example, pipeline_status evidence should say
        # "Approved", not "building_permit_issued".
        if not values_contradict(
            field_name,
            override_value,
            field_payload.get("value"),
            session=session,
        ):
            supporting.append(evidence.id)
    return supporting


def _winning_candidate_evidence_id(
    session: Session,
    resolution: FieldResolution,
) -> uuid.UUID | None:
    candidate_ids = resolution.metadata.get("candidate_evidence_ids") or []
    if not candidate_ids:
        return None
    try:
        evidence_id = uuid.UUID(str(candidate_ids[0]))
    except (TypeError, ValueError):
        return None
    return session.execute(
        select(Evidence.id).where(Evidence.id == evidence_id)
    ).scalar_one_or_none()


def _dedupe_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip() if value is not None else ""
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
