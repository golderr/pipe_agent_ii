from __future__ import annotations

import enum
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from tcg_pipeline.db.models import Evidence, StatusConfidence


@dataclass(slots=True, frozen=True)
class FieldObservation:
    field_name: str
    value: Any
    evidence: Evidence
    extracted_confidence: str | None

    @property
    def effective_date(self) -> date:
        return evidence_effective_date(self.evidence)


@dataclass(slots=True)
class FieldResolution:
    field_name: str
    value: Any
    confidence: StatusConfidence
    evidence_ids: list[UUID] = dataclass_field(default_factory=list)
    rule_applied: str = "unresolved"
    evidence_date: date | None = None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)
    notes: list[str] = dataclass_field(default_factory=list)


def build_resolution(
    field_name: str,
    value: Any,
    *,
    confidence: StatusConfidence,
    observations: list[FieldObservation] | None = None,
    rule_applied: str,
    metadata: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> FieldResolution:
    observations = observations or []
    metadata_payload = dict(metadata or {})
    if observations:
        winner = observations[0]
        metadata_payload.setdefault(
            "evidence_frontier",
            {
                "evidence_date": winner.effective_date,
                "collected_at": winner.evidence.collected_at,
                "source_tier": winner.evidence.source_tier,
                "source_type": winner.evidence.source_type,
                "evidence_ids": [observation.evidence.id for observation in observations],
            },
        )
    return FieldResolution(
        field_name=field_name,
        value=value,
        confidence=confidence,
        evidence_ids=[observation.evidence.id for observation in observations],
        rule_applied=rule_applied,
        evidence_date=observations[0].effective_date if observations else None,
        metadata=metadata_payload,
        notes=notes or [],
    )


def evidence_effective_date(evidence: Evidence) -> date:
    """Date used for evidence ordering.

    Some seed sources carry future projected event dates, especially CoStar delivery
    dates. Those dates are field values, not proof that unrelated fields are newer
    evidence, so future row-level evidence dates are capped at collection date.
    """
    collected_date = evidence.collected_at.date()
    if evidence.evidence_date is None:
        return collected_date
    if evidence.evidence_date > collected_date:
        return collected_date
    return evidence.evidence_date


def resolve_override(
    field_name: str,
    overrides: dict[str, Any] | None,
) -> FieldResolution | None:
    if not overrides or field_name not in overrides:
        return None

    override_payload = overrides[field_name]
    if isinstance(override_payload, dict) and "value" in override_payload:
        value = override_payload["value"]
        metadata = {
            key: override_payload.get(key)
            for key in ("set_by", "set_at", "note")
            if key in override_payload
        }
    else:
        value = override_payload
        metadata = {"set_by": "legacy", "set_at": None, "note": None}

    return build_resolution(
        field_name,
        value,
        confidence=StatusConfidence.HIGH,
        rule_applied="researcher_override",
        metadata=metadata,
    )


def apply_override(
    field_name: str,
    candidate: FieldResolution,
    overrides: dict[str, Any] | None,
    *,
    transform_value=None,
    source_priority: dict[str, int] | None = None,
) -> FieldResolution:
    if not overrides or field_name not in overrides:
        return candidate

    override_payload = _normalize_override_payload(overrides[field_name])
    override_value = override_payload["value"]
    if transform_value is not None:
        override_value = transform_value(override_value)

    mode = override_payload.get("mode") or "sticky"
    baseline = override_payload.get("baseline")
    candidate_is_newer = _candidate_is_newer(
        candidate,
        baseline,
        source_priority=source_priority,
    )

    return build_resolution(
        field_name,
        override_value,
        confidence=StatusConfidence.HIGH,
        rule_applied=(
            "researcher_override_until_newer_evidence"
            if mode == "until_newer_evidence"
            else "researcher_override"
        ),
        metadata={
            "set_by": override_payload.get("set_by"),
            "set_at": override_payload.get("set_at"),
            "note": override_payload.get("note"),
            "mode": mode,
            "baseline": baseline,
            "candidate_value": normalize_comparable(candidate.value),
            "candidate_rule_applied": candidate.rule_applied,
            "candidate_confidence": candidate.confidence.value,
            "candidate_evidence_ids": [str(evidence_id) for evidence_id in candidate.evidence_ids],
            "candidate_evidence_date": (
                candidate.evidence_date.isoformat()
                if candidate.evidence_date is not None
                else None
            ),
            "candidate_evidence_frontier": candidate.metadata.get("evidence_frontier"),
            "candidate_is_newer_than_baseline": candidate_is_newer,
        },
    )


def iter_field_observations(
    evidence_rows: list[Evidence],
    field_name: str,
) -> list[FieldObservation]:
    observations: list[FieldObservation] = []
    for evidence in evidence_rows:
        extracted = evidence.extracted_fields or {}
        field_payload = extracted.get(field_name)
        if not isinstance(field_payload, dict) or "value" not in field_payload:
            continue
        value = field_payload["value"]
        if not _has_meaningful_value(value):
            continue
        observations.append(
            FieldObservation(
                field_name=field_name,
                value=value,
                evidence=evidence,
                extracted_confidence=_coerce_text(field_payload.get("confidence")),
            )
        )
    return sort_observations(observations)


def sort_observations(
    observations: list[FieldObservation],
    *,
    source_priority: dict[str, int] | None = None,
) -> list[FieldObservation]:
    # "Most recent wins" means event date first, then collection time, with
    # source preference only breaking remaining temporal ties.
    return sorted(
        observations,
        key=lambda observation: (
            -_sort_ordinal(observation.effective_date),
            -int(observation.evidence.collected_at.timestamp()),
            (source_priority or {}).get(observation.evidence.source_type, 99),
            observation.evidence.source_tier,
        ),
    )


def infer_confidence(
    observations: list[FieldObservation],
    *,
    freshness_days: int = 180,
) -> StatusConfidence:
    if not observations:
        return StatusConfidence.LOW

    explicit_confidence = _coerce_status_confidence(observations[0].extracted_confidence)
    if explicit_confidence is not None:
        return explicit_confidence

    newest = observations[0]
    if newest.evidence.source_tier == 1:
        return StatusConfidence.HIGH

    if _is_fresh(newest.effective_date, freshness_days=freshness_days):
        if len({observation.evidence.source_type for observation in observations[:2]}) >= 2:
            return StatusConfidence.HIGH
        if newest.evidence.source_tier in {2, 3}:
            return StatusConfidence.MEDIUM

    return StatusConfidence.LOW


def parse_date_value(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_comparable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): normalize_comparable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_comparable(item) for item in value]
    return value


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_status_confidence(value: str | None) -> StatusConfidence | None:
    if not value:
        return None
    try:
        return StatusConfidence(value)
    except ValueError:
        return None


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _is_fresh(value: date, *, freshness_days: int) -> bool:
    return value >= date.today() - timedelta(days=freshness_days)


def _sort_ordinal(value: date) -> int:
    return value.toordinal()


def _normalize_override_payload(override_payload: Any) -> dict[str, Any]:
    if isinstance(override_payload, dict) and "value" in override_payload:
        return {
            "value": override_payload.get("value"),
            "set_by": override_payload.get("set_by"),
            "set_at": override_payload.get("set_at"),
            "note": override_payload.get("note"),
            "mode": override_payload.get("mode"),
            "baseline": (
                override_payload.get("baseline")
                if isinstance(override_payload.get("baseline"), dict)
                else None
            ),
        }
    return {
        "value": override_payload,
        "set_by": "legacy",
        "set_at": None,
        "note": None,
        "mode": "sticky",
        "baseline": None,
    }


def _candidate_is_newer(
    candidate: FieldResolution,
    baseline: Any,
    *,
    source_priority: dict[str, int] | None = None,
) -> bool:
    if not isinstance(baseline, dict):
        return False

    candidate_frontier = _normalize_frontier(
        candidate.metadata.get("evidence_frontier"),
        source_priority=source_priority,
    )
    baseline_frontier = _normalize_frontier(
        baseline,
        source_priority=source_priority,
    )
    if candidate_frontier is None or baseline_frontier is None:
        return False
    return candidate_frontier > baseline_frontier


def _normalize_frontier(
    frontier: Any,
    *,
    source_priority: dict[str, int] | None = None,
) -> tuple[int, float, int, int] | None:
    if not isinstance(frontier, dict):
        return None

    evidence_date = parse_date_value(frontier.get("evidence_date"))
    collected_at = _parse_datetime_value(frontier.get("collected_at"))
    source_type = _coerce_text(frontier.get("source_type"))
    source_tier_value = frontier.get("source_tier")
    try:
        source_tier = int(source_tier_value)
    except (TypeError, ValueError):
        return None

    if evidence_date is None or collected_at is None:
        return None
    priority = (source_priority or {}).get(source_type or "", 99)
    return (
        evidence_date.toordinal(),
        collected_at.timestamp(),
        -priority,
        -source_tier,
    )


def _parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
