from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.models import Evidence, Project, ProjectSourceRecord
from tcg_pipeline.semantic.ladbs import ladbs_semantic_metadata_by_field
from tcg_pipeline.semantic.types import SemanticInterpretation
from tcg_pipeline.source_tiers import get_logical_source_type, get_source_tier

PIPEDREAM_SNAPSHOT_FIELDS = (
    "canonical_address",
    "raw_addresses",
    "lat",
    "lng",
    "city",
    "state",
    "county",
    "zip",
    "tcg_region",
    "jurisdiction",
    "project_name",
    "previous_names",
    "developer",
    "rent_or_sale",
    "product_type",
    "age_restriction",
    "stories",
    "total_units",
    "market_rate_units",
    "affordable_units",
    "workforce_units",
    "pct_studio",
    "pct_1bed",
    "pct_2bed",
    "pct_other_bed",
    "acres",
    "retail_sf",
    "office_sf",
    "hotel_keys",
    "pipeline_status",
    "status_date",
    "date_delivery",
    "planner_1_name",
    "planner_1_city",
    "planner_1_email",
    "planner_1_phone",
    "planner_2_name",
    "planner_2_city",
    "planner_2_email",
    "planner_2_phone",
    "source_urls",
    "last_editor",
    "last_edit_date",
)
FIELD_DATE_KEYS = (
    "status_evidence_date",
    "permit_issue_date",
    "cofo_issue_date",
    "inspection_date",
    "status_date",
    "date_construction_start",
)
# date_delivery is often a future projection. It remains an extracted field value,
# but it should not make unrelated fields look like future-dated evidence.


@dataclass(slots=True)
class EvidenceWriteResult:
    inserted: bool = False
    linked: bool = False
    evidence: Evidence | None = None

    @property
    def changed(self) -> bool:
        return self.inserted or self.linked


def write_raw_record_evidence(
    session: Session,
    *,
    raw_record: RawRecord,
    project_id,
    collected_at: datetime,
    ingest_method: str,
    notes: str | None = None,
) -> EvidenceWriteResult:
    return write_evidence(
        session,
        project_id=project_id,
        source_name=raw_record.source_name,
        source_record_id=raw_record.source_record_id,
        raw_data=raw_record.raw_payload,
        mapped_fields=raw_record.mapped_fields,
        collected_at=collected_at,
        source_created_at=raw_record.source_created_at,
        source_updated_at=raw_record.source_updated_at,
        raw_data_hash=raw_record.source_row_hash,
        ingest_method=ingest_method,
        notes=notes,
    )


def write_source_record_evidence(
    session: Session,
    *,
    project_id,
    source_record: ProjectSourceRecord,
    ingest_method: str,
    notes: str | None = None,
) -> EvidenceWriteResult:
    return write_evidence(
        session,
        project_id=project_id,
        source_name=source_record.source_name,
        source_record_id=source_record.source_record_id,
        raw_data=source_record.raw_payload,
        mapped_fields=source_record.mapped_fields or {},
        collected_at=derive_source_record_collected_at(source_record),
        source_created_at=source_record.source_created_at,
        source_updated_at=source_record.source_updated_at,
        first_seen_at=source_record.first_seen_at,
        raw_data_hash=source_record.source_row_hash,
        ingest_method=ingest_method,
        notes=notes,
    )


def write_pipedream_snapshot_evidence(
    session: Session,
    *,
    project: Project,
    source_record: ProjectSourceRecord | None,
    ingest_method: str,
    notes: str | None = None,
) -> EvidenceWriteResult:
    snapshot_values = {
        field_name: getattr(project, field_name) for field_name in PIPEDREAM_SNAPSHOT_FIELDS
    }
    return write_evidence(
        session,
        project_id=project.id,
        source_name="pipedream",
        source_record_id=source_record.source_record_id if source_record is not None else None,
        raw_data=source_record.raw_payload if source_record is not None else None,
        mapped_fields=snapshot_values,
        extracted_fields=wrap_extracted_fields(snapshot_values),
        collected_at=derive_pipedream_collected_at(project, source_record),
        source_created_at=project.created_at,
        source_updated_at=source_record.source_updated_at if source_record is not None else None,
        first_seen_at=(
            source_record.first_seen_at if source_record is not None else project.created_at
        ),
        ingest_method=ingest_method,
        evidence_date=derive_pipedream_evidence_date(project, source_record),
        notes=notes,
    )


def write_evidence(
    session: Session,
    *,
    project_id,
    source_name: str,
    source_record_id: str | None,
    raw_data: Mapping[str, Any] | None,
    mapped_fields: Mapping[str, Any] | None,
    collected_at: datetime,
    ingest_method: str,
    extracted_fields: dict[str, dict[str, Any]] | None = None,
    source_created_at: datetime | None = None,
    source_updated_at: datetime | None = None,
    first_seen_at: datetime | None = None,
    raw_data_hash: str | None = None,
    evidence_date: date | None = None,
    notes: str | None = None,
) -> EvidenceWriteResult:
    source_type = get_logical_source_type(source_name)
    wrapped_fields = extracted_fields or wrap_extracted_fields(dict(mapped_fields or {}))
    if extracted_fields is None:
        _apply_ladbs_semantic_metadata(
            wrapped_fields,
            source_name=source_name,
            mapped_fields=mapped_fields or {},
        )
    serialized_raw_data = serialize_json(raw_data) if raw_data is not None else None
    effective_raw_data_hash = raw_data_hash or compute_evidence_hash(
        raw_data=serialized_raw_data,
        extracted_fields=wrapped_fields,
    )
    existing = _find_existing_evidence(
        session,
        project_id=project_id,
        source_type=source_type,
        source_record_id=source_record_id,
        raw_data_hash=effective_raw_data_hash,
    )
    if existing is not None:
        if existing.project_id is None and project_id is not None:
            existing.project_id = project_id
            return EvidenceWriteResult(inserted=False, linked=True, evidence=existing)
        return EvidenceWriteResult(inserted=False, linked=False, evidence=existing)

    evidence = Evidence(
        project_id=project_id,
        source_type=source_type,
        source_tier=get_source_tier(source_type),
        ingest_method=ingest_method,
        source_record_id=source_record_id,
        collected_at=collected_at,
        evidence_date=(
            evidence_date
            or derive_evidence_date(
                mapped_fields or {},
                source_updated_at=source_updated_at,
                source_created_at=source_created_at,
                first_seen_at=first_seen_at,
            )
        ),
        raw_data=serialized_raw_data,
        raw_data_hash=effective_raw_data_hash,
        extracted_fields=wrapped_fields or None,
        notes=notes,
    )
    session.add(evidence)
    return EvidenceWriteResult(inserted=True, linked=False, evidence=evidence)


def wrap_extracted_fields(fields: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    wrapped: dict[str, dict[str, Any]] = {}
    for field_name, value in fields.items():
        serialized = serialize_json(value)
        if not has_meaningful_value(serialized):
            continue
        wrapped[str(field_name)] = {"value": serialized, "confidence": None}
    return wrapped


def _apply_ladbs_semantic_metadata(
    wrapped_fields: dict[str, dict[str, Any]],
    *,
    source_name: str,
    mapped_fields: Mapping[str, Any],
) -> None:
    for field_name, interpretation in ladbs_semantic_metadata_by_field(
        source_name=source_name,
        mapped_fields=mapped_fields,
    ).items():
        if field_name not in wrapped_fields:
            continue
        wrapped_fields[field_name].update(_semantic_field_payload(interpretation))


def _semantic_field_payload(interpretation: SemanticInterpretation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "confidence": interpretation.confidence,
        "semantic": {
            "reason_code": interpretation.reason_code,
            "requires_corroboration": interpretation.requires_corroboration,
            "promotes_status_alone": False,
            "review_item_template": None,
        },
    }
    highlights = [
        {
            "field": anchor.field_name or interpretation.field_name,
            "value": serialize_json(interpretation.canonical_value),
            "passage": anchor.text,
            "offset_start": anchor.offset_start,
            "offset_end": anchor.offset_end,
            "reason_code": interpretation.reason_code,
        }
        for anchor in interpretation.source_anchors
    ]
    if highlights:
        payload["highlights"] = highlights
    return payload


def derive_evidence_date(
    fields: Mapping[str, Any],
    *,
    source_updated_at: datetime | None = None,
    source_created_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> date | None:
    for field_name in FIELD_DATE_KEYS:
        parsed = coerce_date(fields.get(field_name))
        if parsed is not None:
            return parsed
    for timestamp in (source_updated_at, source_created_at, first_seen_at):
        if timestamp is not None:
            return timestamp.date()
    return None


def derive_source_record_collected_at(source_record: ProjectSourceRecord) -> datetime:
    return (
        source_record.last_pulled_at
        or source_record.last_seen_at
        or source_record.first_seen_at
        or datetime.now(UTC)
    )


def derive_pipedream_collected_at(
    project: Project,
    source_record: ProjectSourceRecord | None,
) -> datetime:
    if source_record is not None:
        return derive_source_record_collected_at(source_record)
    return project.created_at


def derive_pipedream_evidence_date(
    project: Project,
    source_record: ProjectSourceRecord | None,
) -> date | None:
    for candidate in (project.last_edit_date, project.status_date, project.created_at.date()):
        if candidate is not None:
            return candidate
    if source_record is not None:
        return derive_evidence_date(
            source_record.mapped_fields or {},
            source_updated_at=source_record.source_updated_at,
            source_created_at=source_record.source_created_at,
            first_seen_at=source_record.first_seen_at,
        )
    return None


def compute_evidence_hash(
    *,
    raw_data: dict[str, Any] | None,
    extracted_fields: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "raw_data": raw_data,
        "extracted_fields": extracted_fields,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def coerce_date(value: Any) -> date | None:
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


def serialize_json(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): serialize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_json(item) for item in value]
    return value


def has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _find_existing_evidence(
    session: Session,
    *,
    project_id,
    source_type: str,
    source_record_id: str | None,
    raw_data_hash: str,
) -> Evidence | None:
    if source_record_id is None:
        return _find_existing_for_project(
            session,
            project_id=project_id,
            source_type=source_type,
            source_record_id=None,
            raw_data_hash=raw_data_hash,
        )

    existing_for_project = _find_existing_for_project(
        session,
        project_id=project_id,
        source_type=source_type,
        source_record_id=source_record_id,
        raw_data_hash=raw_data_hash,
    )
    if existing_for_project is not None:
        return existing_for_project

    if project_id is None:
        return None

    return _find_existing_for_project(
        session,
        project_id=None,
        source_type=source_type,
        source_record_id=source_record_id,
        raw_data_hash=raw_data_hash,
    )


def _find_existing_for_project(
    session: Session,
    *,
    project_id,
    source_type: str,
    source_record_id: str | None,
    raw_data_hash: str,
) -> Evidence | None:
    statement = select(Evidence).where(
        Evidence.source_type == source_type,
        Evidence.raw_data_hash == raw_data_hash,
    )
    if source_record_id is None:
        statement = statement.where(Evidence.source_record_id.is_(None))
    else:
        statement = statement.where(Evidence.source_record_id == source_record_id)

    if project_id is None:
        statement = statement.where(Evidence.project_id.is_(None))
    else:
        statement = statement.where(Evidence.project_id == project_id)

    return session.execute(statement.limit(1)).scalars().first()
