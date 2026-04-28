from __future__ import annotations

import argparse
import enum
import hashlib
import json
import sys
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tcg_pipeline.db.connection import get_session_factory  # noqa: E402
from tcg_pipeline.db.models import (  # noqa: E402
    Evidence,
    IdentifierType,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
)
from tcg_pipeline.ingesters.pipedream import PIPEDREAM_CREATED_BY  # noqa: E402
from tcg_pipeline.review.contradictions import detect_contradictions  # noqa: E402
from tcg_pipeline.source_tiers import get_logical_source_type, get_source_tier  # noqa: E402

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
# date_delivery is intentionally excluded from row-level evidence dates. It is often
# a future projection in CoStar; using it as evidence freshness makes unrelated
# fields (status, developer, units) appear newer than they are.


@dataclass(slots=True)
class BackfillEvidenceResult:
    inserted_source_record_rows: int = 0
    inserted_pipedream_snapshots: int = 0
    skipped_duplicates: int = 0
    skipped_pipedream_source_records: int = 0
    affected_project_ids: set[Any] = dataclass_field(default_factory=set)
    contradiction_review_items_created: int = 0
    contradiction_review_items_updated: int = 0
    contradiction_review_items_invalidated: int = 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill append-only evidence rows from existing project source records.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build evidence rows and print counts without committing changes.",
    )
    args = parser.parse_args()

    result = backfill_evidence(dry_run=args.dry_run)
    print(f"Inserted PSR evidence rows: {result.inserted_source_record_rows}")
    print(f"Inserted pipedream snapshot rows: {result.inserted_pipedream_snapshots}")
    print(f"Skipped duplicate evidence rows: {result.skipped_duplicates}")
    print(f"Skipped pipedream PSR rows: {result.skipped_pipedream_source_records}")
    print(f"Affected projects scanned for contradictions: {len(result.affected_project_ids)}")
    print(
        "Contradiction review items: "
        f"created={result.contradiction_review_items_created}, "
        f"updated={result.contradiction_review_items_updated}, "
        f"invalidated={result.contradiction_review_items_invalidated}"
    )
    print(f"Committed: {not args.dry_run}")


def backfill_evidence(*, dry_run: bool = False) -> BackfillEvidenceResult:
    session_factory = get_session_factory()
    with session_factory() as session:
        result = BackfillEvidenceResult()
        result = _backfill_source_record_evidence(session, result=result)
        result = _backfill_pipedream_snapshots(session, result=result)
        result = _detect_backfill_contradictions(session, result=result)
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result


def _backfill_source_record_evidence(
    session: Session,
    *,
    result: BackfillEvidenceResult,
) -> BackfillEvidenceResult:
    source_records = session.execute(
        select(ProjectSourceRecord).order_by(
            ProjectSourceRecord.first_seen_at,
            ProjectSourceRecord.id,
        )
    ).scalars().all()
    for source_record in source_records:
        source_type = get_logical_source_type(source_record.source_name)
        if source_type == "pipedream":
            result.skipped_pipedream_source_records += 1
            continue

        extracted_fields = _wrap_extracted_fields(source_record.mapped_fields or {})
        raw_data = _serialize_json(source_record.raw_payload)
        raw_data_hash = source_record.source_row_hash or _compute_evidence_hash(
            raw_data=raw_data,
            extracted_fields=extracted_fields,
        )
        source_record_id = source_record.source_record_id

        if _evidence_exists(
            session,
            project_id=source_record.project_id,
            source_type=source_type,
            source_record_id=source_record_id,
            raw_data_hash=raw_data_hash,
        ):
            result.skipped_duplicates += 1
            continue

        session.add(
            Evidence(
                project_id=source_record.project_id,
                source_type=source_type,
                source_tier=get_source_tier(source_type),
                ingest_method=_backfill_ingest_method(source_type),
                source_record_id=source_record_id,
                collected_at=_derive_source_record_collected_at(source_record),
                evidence_date=_derive_source_record_evidence_date(source_record),
                raw_data=raw_data,
                raw_data_hash=raw_data_hash,
                extracted_fields=extracted_fields or None,
                notes="Backfilled from project_source_records.",
            )
        )
        result.inserted_source_record_rows += 1
        if source_record.project_id is not None:
            result.affected_project_ids.add(source_record.project_id)

    return result


def _backfill_pipedream_snapshots(
    session: Session,
    *,
    result: BackfillEvidenceResult,
) -> BackfillEvidenceResult:
    pipedream_id_rows = session.execute(
        select(ProjectIdentifier.project_id, ProjectIdentifier.value).where(
            ProjectIdentifier.identifier_type == IdentifierType.TCG_PIPEDREAM_ID
        )
    ).all()
    pipedream_id_by_project_id = {
        row.project_id: row.value
        for row in pipedream_id_rows
        if row.project_id is not None and row.value
    }
    pipedream_source_record_by_project_id = {
        source_record.project_id: source_record
        for source_record in session.execute(
            select(ProjectSourceRecord).where(ProjectSourceRecord.source_name == "pipedream")
        ).scalars().all()
    }
    project_ids = set(pipedream_id_by_project_id) | set(pipedream_source_record_by_project_id)
    created_project_ids = session.execute(
        select(Project.id).where(Project.created_by == PIPEDREAM_CREATED_BY)
    ).scalars().all()
    project_ids.update(created_project_ids)
    if not project_ids:
        return result

    projects = session.execute(
        select(Project).where(Project.id.in_(sorted(project_ids, key=str)))
    ).scalars().all()
    for project in projects:
        source_record = pipedream_source_record_by_project_id.get(project.id)
        source_record_id = pipedream_id_by_project_id.get(project.id)
        if source_record_id is None and source_record is not None:
            source_record_id = source_record.source_record_id

        extracted_fields = _build_pipedream_snapshot_fields(project)
        raw_data = _serialize_json(source_record.raw_payload) if source_record is not None else None
        raw_data_hash = _compute_evidence_hash(
            raw_data=raw_data,
            extracted_fields=extracted_fields,
        )
        if _evidence_exists(
            session,
            project_id=project.id,
            source_type="pipedream",
            source_record_id=source_record_id,
            raw_data_hash=raw_data_hash,
        ):
            result.skipped_duplicates += 1
            continue

        session.add(
            Evidence(
                project_id=project.id,
                source_type="pipedream",
                source_tier=get_source_tier("pipedream"),
                ingest_method="seed_import",
                source_record_id=source_record_id,
                collected_at=_derive_pipedream_collected_at(project, source_record),
                evidence_date=_derive_pipedream_evidence_date(project, source_record),
                raw_data=raw_data,
                raw_data_hash=raw_data_hash,
                extracted_fields=extracted_fields or None,
                notes=(
                    "Synthesized from pipedream-seeded project snapshot "
                    "during evidence backfill."
                ),
            )
        )
        result.inserted_pipedream_snapshots += 1
        result.affected_project_ids.add(project.id)

    return result


def _detect_backfill_contradictions(
    session: Session,
    *,
    result: BackfillEvidenceResult,
) -> BackfillEvidenceResult:
    if not result.affected_project_ids:
        return result

    session.flush()
    contradiction_result = detect_contradictions(session, result.affected_project_ids)
    result.contradiction_review_items_created = contradiction_result.created_count
    result.contradiction_review_items_updated = contradiction_result.updated_count
    result.contradiction_review_items_invalidated = contradiction_result.invalidated_count
    return result


def _evidence_exists(
    session: Session,
    *,
    project_id,
    source_type: str,
    source_record_id: str | None,
    raw_data_hash: str,
) -> bool:
    # The partial unique index on evidence protects the common non-null source_record_id path.
    # This existence check keeps reruns idempotent and also treats pre-existing orphan
    # evidence as duplicates when the source row is already known.
    if _evidence_exists_for_project(
        session,
        project_id=project_id,
        source_type=source_type,
        source_record_id=source_record_id,
        raw_data_hash=raw_data_hash,
    ):
        return True

    if project_id is not None and source_record_id is not None:
        return _evidence_exists_for_project(
            session,
            project_id=None,
            source_type=source_type,
            source_record_id=source_record_id,
            raw_data_hash=raw_data_hash,
        )

    return False


def _evidence_exists_for_project(
    session: Session,
    *,
    project_id,
    source_type: str,
    source_record_id: str | None,
    raw_data_hash: str,
) -> bool:
    with session.no_autoflush:
        statement = select(Evidence.id).where(
            Evidence.source_type == source_type,
            Evidence.raw_data_hash == raw_data_hash,
        )
        if project_id is None:
            statement = statement.where(Evidence.project_id.is_(None))
        else:
            statement = statement.where(Evidence.project_id == project_id)

        if source_record_id is None:
            statement = statement.where(Evidence.source_record_id.is_(None))
        else:
            statement = statement.where(Evidence.source_record_id == source_record_id)

        return session.execute(statement.limit(1)).scalar_one_or_none() is not None


def _wrap_extracted_fields(fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    wrapped: dict[str, dict[str, Any]] = {}
    for field_name, value in fields.items():
        serialized = _serialize_json(value)
        if not _has_meaningful_value(serialized):
            continue
        wrapped[field_name] = {"value": serialized, "confidence": None}
    return wrapped


def _build_pipedream_snapshot_fields(project: Project) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, Any] = {}
    for field_name in PIPEDREAM_SNAPSHOT_FIELDS:
        snapshot[field_name] = getattr(project, field_name)
    return _wrap_extracted_fields(snapshot)


def _derive_source_record_evidence_date(source_record: ProjectSourceRecord) -> date | None:
    mapped_fields = source_record.mapped_fields or {}
    for field_name in FIELD_DATE_KEYS:
        parsed = _coerce_date(mapped_fields.get(field_name))
        if parsed is not None:
            return parsed
    for timestamp in (
        source_record.source_updated_at,
        source_record.source_created_at,
        source_record.first_seen_at,
    ):
        if timestamp is not None:
            return timestamp.date()
    return None


def _derive_pipedream_evidence_date(
    project: Project,
    source_record: ProjectSourceRecord | None,
) -> date | None:
    for candidate in (project.last_edit_date, project.status_date, project.created_at.date()):
        if candidate is not None:
            return candidate
    if source_record is not None:
        return _derive_source_record_evidence_date(source_record)
    return None


def _derive_source_record_collected_at(source_record: ProjectSourceRecord) -> datetime:
    return (
        source_record.last_pulled_at
        or source_record.last_seen_at
        or source_record.first_seen_at
        or datetime.now(UTC)
    )


def _derive_pipedream_collected_at(
    project: Project,
    source_record: ProjectSourceRecord | None,
) -> datetime:
    if source_record is not None:
        return _derive_source_record_collected_at(source_record)
    return project.created_at


def _backfill_ingest_method(source_type: str) -> str:
    if source_type in {"pipedream", "costar"}:
        return "seed_import"
    return "scheduled_collector"


def _compute_evidence_hash(
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


def _coerce_date(value: Any) -> date | None:
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


def _serialize_json(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_json(item) for item in value]
    return value


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


if __name__ == "__main__":
    main()
