from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.models import (
    IdentifierType,
    Priority,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.ingesters._common import serialize_json_value
from tcg_pipeline.matching.differ import (
    DetectedChange,
    DiffResult,
    StatusSuggestion,
    diff_project_against_record,
)
from tcg_pipeline.matching.matcher import MatchResult, match_raw_record


@dataclass(slots=True)
class CollectPersistResult:
    source_run_id: uuid.UUID
    records_pulled: int = 0
    matched_existing_projects: int = 0
    matched_by_source_record: int = 0
    matched_by_identifier: int = 0
    matched_by_address: int = 0
    inserted_source_records: int = 0
    updated_source_records: int = 0
    inserted_identifiers: int = 0
    new_candidate_review_items: int = 0
    status_change_review_items: int = 0
    possible_match_review_items: int = 0


def persist_collected_records(
    session: Session,
    *,
    market: str,
    source_name: str,
    raw_records: list[RawRecord],
) -> CollectPersistResult:
    run_started_at = datetime.now(UTC)
    source_run = SourceRun(
        market=market,
        source_name=source_name,
        records_pulled=len(raw_records),
    )
    session.add(source_run)
    session.flush()

    result = CollectPersistResult(
        source_run_id=source_run.id,
        records_pulled=len(raw_records),
    )

    for raw_record in raw_records:
        match_result = match_raw_record(session, market=market, raw_record=raw_record)
        if match_result.project_id is None:
            _create_unmatched_review_item(
                session,
                source_run=source_run,
                raw_record=raw_record,
                match_result=match_result,
                result=result,
            )
            continue

        project = session.get(Project, match_result.project_id)
        if project is None:
            continue

        result.matched_existing_projects += 1
        _increment_match_counter(result, match_result)
        was_existing_source_record = _upsert_source_record(
            session,
            project=project,
            raw_record=raw_record,
            source_run_timestamp=run_started_at,
        )
        if was_existing_source_record:
            result.updated_source_records += 1
        else:
            result.inserted_source_records += 1
            source_run.new_matches += 1

        result.inserted_identifiers += _persist_identifiers(
            session,
            project=project,
            raw_record=raw_record,
        )

        diff_result = diff_project_against_record(project, raw_record)
        if diff_result.has_reviewable_changes:
            source_run.updates_found += 1
            result.status_change_review_items += 1
            session.add(
                ReviewItem(
                    project_id=project.id,
                    source_run_id=source_run.id,
                    item_type=ReviewItemType.STATUS_CHANGE,
                    status=ReviewItemStatus.OPEN,
                    priority=_review_priority(diff_result),
                    match_confidence=match_result.confidence,
                    payload={
                        "match": _serialize_match_result(match_result),
                        "source_record_id": raw_record.source_record_id,
                        "canonical_address": raw_record.canonical_address,
                        "mapped_fields": _serialize_payload(raw_record.mapped_fields),
                        "changes": [
                            _serialize_change(change) for change in diff_result.field_changes
                        ],
                        "status_suggestion": _serialize_status_suggestion(
                            diff_result.status_suggestion
                        ),
                    },
                )
            )

    source_run.duration_seconds = max(int((datetime.now(UTC) - run_started_at).total_seconds()), 0)
    return result


def _increment_match_counter(result: CollectPersistResult, match_result: MatchResult) -> None:
    if match_result.match_type == "source_record":
        result.matched_by_source_record += 1
    elif match_result.match_type == "identifier":
        result.matched_by_identifier += 1
    elif match_result.match_type == "address":
        result.matched_by_address += 1


def _upsert_source_record(
    session: Session,
    *,
    project: Project,
    raw_record: RawRecord,
    source_run_timestamp: datetime,
) -> bool:
    source_record = session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.source_name == raw_record.source_name,
            ProjectSourceRecord.source_record_id == raw_record.source_record_id,
        )
    ).scalar_one_or_none()

    if source_record is None:
        session.add(
            ProjectSourceRecord(
                project_id=project.id,
                source_name=raw_record.source_name,
                source_record_id=raw_record.source_record_id,
                first_seen_at=source_run_timestamp,
                last_seen_at=source_run_timestamp,
                last_pulled_at=source_run_timestamp,
                raw_payload=_serialize_payload(raw_record.raw_payload),
                mapped_fields=_serialize_payload(raw_record.mapped_fields),
                field_provenance={key: raw_record.source_name for key in raw_record.mapped_fields},
            )
        )
        return False

    source_record.project_id = project.id
    source_record.last_seen_at = source_run_timestamp
    source_record.last_pulled_at = source_run_timestamp
    source_record.raw_payload = _serialize_payload(raw_record.raw_payload)
    source_record.mapped_fields = _serialize_payload(raw_record.mapped_fields)
    source_record.field_provenance = {
        key: raw_record.source_name for key in raw_record.mapped_fields
    }
    return True


def _persist_identifiers(session: Session, *, project: Project, raw_record: RawRecord) -> int:
    inserted_count = 0
    for identifier_type_name, values in raw_record.identifiers.items():
        identifier_type = _coerce_identifier_type(identifier_type_name)
        if identifier_type is None:
            continue
        for value in sorted({value for value in values if value}):
            existing = session.execute(
                select(ProjectIdentifier.id).where(
                    ProjectIdentifier.project_id == project.id,
                    ProjectIdentifier.identifier_type == identifier_type,
                    ProjectIdentifier.value == value,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            session.add(
                ProjectIdentifier(
                    project_id=project.id,
                    identifier_type=identifier_type,
                    value=value,
                )
            )
            inserted_count += 1
    return inserted_count


def _create_unmatched_review_item(
    session: Session,
    *,
    source_run: SourceRun,
    raw_record: RawRecord,
    match_result: MatchResult,
    result: CollectPersistResult,
) -> None:
    if match_result.candidate_project_ids:
        item_type = ReviewItemType.POSSIBLE_MATCH
        priority = Priority.MEDIUM
        result.possible_match_review_items += 1
    else:
        item_type = ReviewItemType.NEW_CANDIDATE
        priority = _priority_for_candidate(raw_record)
        result.new_candidate_review_items += 1
        source_run.new_candidates += 1

    session.add(
        ReviewItem(
            project_id=None,
            source_run_id=source_run.id,
            item_type=item_type,
            status=ReviewItemStatus.OPEN,
            priority=priority,
            match_confidence=match_result.confidence,
            payload={
                "match": _serialize_match_result(match_result),
                "source_record_id": raw_record.source_record_id,
                "canonical_address": raw_record.canonical_address,
                "mapped_fields": _serialize_payload(raw_record.mapped_fields),
                "raw_payload": _serialize_payload(raw_record.raw_payload),
            },
        )
    )


def _review_priority(diff_result: DiffResult) -> Priority:
    if (
        diff_result.status_suggestion is not None
        and diff_result.status_suggestion.priority == Priority.HIGH
    ):
        return Priority.HIGH
    if any(change.priority == Priority.HIGH for change in diff_result.field_changes):
        return Priority.HIGH
    return Priority.MEDIUM


def _priority_for_candidate(raw_record: RawRecord) -> Priority:
    total_units = raw_record.mapped_fields.get("total_units")
    if isinstance(total_units, int) and total_units >= 100:
        return Priority.HIGH
    if isinstance(total_units, int) and total_units >= 25:
        return Priority.MEDIUM
    return Priority.LOW


def _coerce_identifier_type(identifier_type_name: str) -> IdentifierType | None:
    try:
        return IdentifierType(identifier_type_name)
    except ValueError:
        return None


def _serialize_match_result(match_result: MatchResult) -> dict[str, Any]:
    return {
        "match_type": match_result.match_type,
        "confidence": match_result.confidence,
        "candidate_project_ids": [
            str(project_id) for project_id in match_result.candidate_project_ids
        ],
        "matched_identifier_type": (
            match_result.matched_identifier_type.value
            if match_result.matched_identifier_type is not None
            else None
        ),
        "matched_identifier_value": match_result.matched_identifier_value,
    }


def _serialize_change(change: DetectedChange) -> dict[str, Any]:
    return {
        "field": change.field,
        "old_value": serialize_json_value(change.old_value),
        "new_value": serialize_json_value(change.new_value),
        "priority": change.priority.value,
    }


def _serialize_status_suggestion(
    suggestion: StatusSuggestion | None,
) -> dict[str, Any] | None:
    if suggestion is None:
        return None
    return {
        "current_status": suggestion.current_status.value,
        "suggested_status": suggestion.suggested_status.value,
        "evidence_type": suggestion.evidence_type,
        "evidence_date": serialize_json_value(suggestion.evidence_date),
        "reason": suggestion.reason,
        "priority": suggestion.priority.value,
        "rule_code": suggestion.rule_code,
        "proof_level": suggestion.proof_level,
    }


def _serialize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_json_value(value) for key, value in payload.items()}
