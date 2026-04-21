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
        return self.evidence.evidence_date or self.evidence.collected_at.date()


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
    return FieldResolution(
        field_name=field_name,
        value=value,
        confidence=confidence,
        evidence_ids=[observation.evidence.id for observation in observations],
        rule_applied=rule_applied,
        evidence_date=observations[0].effective_date if observations else None,
        metadata=metadata or {},
        notes=notes or [],
    )


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
    return sorted(
        observations,
        key=lambda observation: (
            -_sort_ordinal(observation.effective_date),
            -_sort_ordinal(observation.evidence.collected_at.date()),
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
