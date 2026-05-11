from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.client import build_anthropic_agent_client
from tcg_pipeline.agents.profiles import PERMIT_AGENT_PROFILE, AgentTrigger
from tcg_pipeline.agents.runner import (
    AgentClient,
    AgentRunResult,
    IntakeRecord,
    run_agent_for_intake,
)
from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.evidence import write_raw_record_evidence
from tcg_pipeline.db.models import (
    DismissedRecord,
    IdentifierType,
    Priority,
    ProductType,
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
    ProjectDiffSnapshot,
    ReviewFlag,
    StatusSuggestion,
    diff_project_snapshots,
    snapshot_project_for_diff,
)
from tcg_pipeline.matching.matcher import MatchResult, match_raw_record
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.review.decision_cards import (
    proposed_value_for_payload,
    upsert_decision_card_review_item,
)
from tcg_pipeline.settings import Settings, get_settings
from tcg_pipeline.source_tiers import get_logical_source_type
from tcg_pipeline.status_rules import build_status_suggestion

PERMIT_AGENT_UNIT_DELTA_THRESHOLD = 0.10
PERMIT_AGENT_SOURCE_TYPES = frozenset({"ladbs_permit"})


@dataclass(slots=True)
class CollectPersistResult:
    source_run_id: uuid.UUID
    collection_mode: str = "full"
    incremental_since: datetime | None = None
    source_min_updated_at: datetime | None = None
    source_max_updated_at: datetime | None = None
    records_pulled: int = 0
    matched_existing_projects: int = 0
    matched_by_source_record: int = 0
    matched_by_identifier: int = 0
    matched_by_address: int = 0
    inserted_source_records: int = 0
    updated_source_records: int = 0
    unchanged_source_records: int = 0
    inserted_identifiers: int = 0
    new_candidate_review_items: int = 0
    suppressed_new_candidate_records: int = 0
    dismissed_discovery_records_skipped: int = 0
    status_change_review_items: int = 0
    possible_match_review_items: int = 0


class SourceRecordUpsertOutcome(enum.StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(slots=True)
class PermitAgentRunOutcome:
    client: AgentClient | None
    result: AgentRunResult | None = None


@dataclass(slots=True)
class StatusReviewItemUpsertResult:
    created_count: int = 0
    review_item_ids: list[uuid.UUID] = field(default_factory=list)


def persist_collected_records(
    session: Session,
    *,
    market: str,
    source_name: str,
    raw_records: list[RawRecord],
    collection_mode: str = "full",
    incremental_since: datetime | None = None,
    create_new_candidates: bool = True,
    permit_agent_client: AgentClient | None = None,
    settings: Settings | None = None,
) -> CollectPersistResult:
    run_started_at = datetime.now(UTC)
    resolved_settings = settings or get_settings()
    permit_agent_session_factory = _session_factory_for_current_bind(session)
    resolved_permit_agent_client = permit_agent_client
    identifier_owner_cache: dict[tuple[IdentifierType, str], uuid.UUID] = {}
    source_min_updated_at, source_max_updated_at = _source_updated_at_bounds(raw_records)
    source_run = SourceRun(
        market=market,
        source_name=source_name,
        collection_mode=collection_mode,
        incremental_since=incremental_since,
        source_min_updated_at=source_min_updated_at,
        source_max_updated_at=source_max_updated_at,
        records_pulled=len(raw_records),
    )
    session.add(source_run)
    session.flush()

    result = CollectPersistResult(
        source_run_id=source_run.id,
        collection_mode=collection_mode,
        incremental_since=incremental_since,
        source_min_updated_at=source_min_updated_at,
        source_max_updated_at=source_max_updated_at,
        records_pulled=len(raw_records),
    )

    for raw_record in raw_records:
        match_result = match_raw_record(session, market=market, raw_record=raw_record)
        if match_result.project_id is None:
            if _is_dismissed_source_record(
                session,
                source_name=raw_record.source_name,
                source_record_id=raw_record.source_record_id,
            ):
                result.dismissed_discovery_records_skipped += 1
                if not match_result.candidate_project_ids:
                    result.suppressed_new_candidate_records += 1
                continue
            write_raw_record_evidence(
                session,
                raw_record=raw_record,
                project_id=None,
                collected_at=run_started_at,
                ingest_method="scheduled_collector",
            )
            _create_unmatched_review_item(
                session,
                source_run=source_run,
                raw_record=raw_record,
                match_result=match_result,
                result=result,
                create_new_candidates=create_new_candidates,
            )
            session.flush()
            review_item_ids = _review_item_ids_for_source_record(
                session,
                source_run=source_run,
                raw_record=raw_record,
            )
            resolved_permit_agent_client = _run_permit_agent_for_record(
                raw_record=raw_record,
                match_result=match_result,
                triggers=_permit_agent_triggers_for_unmatched_record(
                    raw_record=raw_record,
                    match_result=match_result,
                    create_new_candidates=create_new_candidates,
                ),
                source_run=source_run,
                review_item_ids=review_item_ids,
                settings=resolved_settings,
                session_factory=permit_agent_session_factory,
                client=resolved_permit_agent_client,
                now=run_started_at,
            ).client
            continue

        project = session.get(Project, match_result.project_id)
        if project is None:
            write_raw_record_evidence(
                session,
                raw_record=raw_record,
                project_id=None,
                collected_at=run_started_at,
                ingest_method="scheduled_collector",
                notes="Matched project id was missing at persistence time.",
            )
            continue

        result.matched_existing_projects += 1
        _increment_match_counter(result, match_result)
        previous_snapshot = snapshot_project_for_diff(project)
        evidence_result = write_raw_record_evidence(
            session,
            raw_record=raw_record,
            project_id=project.id,
            collected_at=run_started_at,
            ingest_method="scheduled_collector",
        )
        upsert_outcome = _upsert_source_record(
            session,
            project=project,
            raw_record=raw_record,
            source_run_timestamp=run_started_at,
        )
        if upsert_outcome == SourceRecordUpsertOutcome.INSERTED:
            result.inserted_source_records += 1
            source_run.new_matches += 1
        elif upsert_outcome == SourceRecordUpsertOutcome.UPDATED:
            result.updated_source_records += 1
        else:
            result.unchanged_source_records += 1
            if not evidence_result.changed:
                continue

        result.inserted_identifiers += _persist_identifiers(
            session,
            project=project,
            raw_record=raw_record,
            identifier_owner_cache=identifier_owner_cache,
        )

        resolution_result = None
        if evidence_result.changed:
            session.flush()
            resolution_result = resolve_project(
                project.id,
                session,
                apply=True,
                write_resolution_log=True,
            )

        diff_result = diff_project_snapshots(
            previous_snapshot,
            snapshot_project_for_diff(project),
            status_evidence_type=_status_evidence_type_from_resolution(resolution_result),
            status_evidence_date=_status_evidence_date_from_resolution(resolution_result),
            status_reason=_status_reason_from_resolution(resolution_result),
            review_flags=_review_flags_from_resolution(resolution_result),
        )
        status_review_item_ids: list[uuid.UUID] = []
        if diff_result.has_reviewable_changes:
            source_run.updates_found += 1
            status_review_items = _upsert_status_change_review_items(
                session,
                project=project,
                source_run=source_run,
                raw_record=raw_record,
                match_result=match_result,
                diff_result=diff_result,
                resolution_result=resolution_result,
                evidence_id=evidence_result.evidence.id if evidence_result.evidence else None,
            )
            status_review_item_ids = status_review_items.review_item_ids
            result.status_change_review_items += status_review_items.created_count
            session.flush()
        resolved_permit_agent_client = _run_permit_agent_for_record(
            raw_record=raw_record,
            match_result=match_result,
            triggers=_permit_agent_triggers_for_matched_record(
                previous_snapshot=previous_snapshot,
                raw_record=raw_record,
            ),
            source_run=source_run,
            project_id=project.id,
            evidence_id=evidence_result.evidence.id if evidence_result.evidence else None,
            review_item_ids=status_review_item_ids,
            settings=resolved_settings,
            session_factory=permit_agent_session_factory,
            client=resolved_permit_agent_client,
            now=run_started_at,
        ).client

    source_run.duration_seconds = max(int((datetime.now(UTC) - run_started_at).total_seconds()), 0)
    return result


def _increment_match_counter(result: CollectPersistResult, match_result: MatchResult) -> None:
    if match_result.match_type == "source_record":
        result.matched_by_source_record += 1
    elif match_result.match_type == "identifier":
        result.matched_by_identifier += 1
    elif match_result.match_type == "address":
        result.matched_by_address += 1


def _session_factory_for_current_bind(session: Session) -> sessionmaker[Session]:
    # Bind the inner sessionmaker to the outer Connection so agent_runs and
    # agent_run_review_items share the collector transaction. The runner's
    # inner Session joins that transaction, so its commit() flushes without
    # issuing a DB-level COMMIT. Do not switch this to get_session_factory();
    # source_run/review_item FK targets would not be visible before outer commit.
    return sessionmaker(
        bind=session.connection(),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def _upsert_source_record(
    session: Session,
    *,
    project: Project,
    raw_record: RawRecord,
    source_run_timestamp: datetime,
) -> SourceRecordUpsertOutcome:
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
                source_row_id=raw_record.source_row_id,
                source_created_at=raw_record.source_created_at,
                source_updated_at=raw_record.source_updated_at,
                source_row_hash=raw_record.source_row_hash,
                first_seen_at=source_run_timestamp,
                last_seen_at=source_run_timestamp,
                last_pulled_at=source_run_timestamp,
                raw_payload=_serialize_payload(raw_record.raw_payload),
                mapped_fields=_serialize_payload(raw_record.mapped_fields),
                field_provenance={key: raw_record.source_name for key in raw_record.mapped_fields},
            )
        )
        return SourceRecordUpsertOutcome.INSERTED

    serialized_raw_payload = _serialize_payload(raw_record.raw_payload)
    serialized_mapped_fields = _serialize_payload(raw_record.mapped_fields)
    is_unchanged = (
        source_record.project_id == project.id
        and raw_record.source_row_hash is not None
        and source_record.source_row_hash is not None
        and raw_record.source_row_hash == source_record.source_row_hash
        and source_record.mapped_fields == serialized_mapped_fields
    )
    source_record.project_id = project.id
    source_record.source_row_id = raw_record.source_row_id
    source_record.source_created_at = raw_record.source_created_at
    source_record.source_updated_at = raw_record.source_updated_at
    source_record.source_row_hash = raw_record.source_row_hash
    source_record.last_seen_at = source_run_timestamp
    source_record.last_pulled_at = source_run_timestamp
    source_record.raw_payload = serialized_raw_payload
    if is_unchanged:
        return SourceRecordUpsertOutcome.UNCHANGED

    source_record.mapped_fields = serialized_mapped_fields
    source_record.field_provenance = {
        key: raw_record.source_name for key in raw_record.mapped_fields
    }
    return SourceRecordUpsertOutcome.UPDATED


def _persist_identifiers(
    session: Session,
    *,
    project: Project,
    raw_record: RawRecord,
    identifier_owner_cache: dict[tuple[IdentifierType, str], uuid.UUID],
) -> int:
    inserted_count = 0
    for identifier_type_name, values in raw_record.identifiers.items():
        identifier_type = _coerce_identifier_type(identifier_type_name)
        if identifier_type is None:
            continue
        for value in sorted({value for value in values if value}):
            cache_key = (identifier_type, value)
            owner_project_id = identifier_owner_cache.get(cache_key)
            if owner_project_id is None:
                owner_project_id = session.execute(
                    select(ProjectIdentifier.project_id).where(
                        ProjectIdentifier.identifier_type == identifier_type,
                        ProjectIdentifier.value == value,
                    )
                ).scalar_one_or_none()
                if owner_project_id is not None:
                    identifier_owner_cache[cache_key] = owner_project_id

            if owner_project_id is not None:
                continue
            session.add(
                ProjectIdentifier(
                    project_id=project.id,
                    identifier_type=identifier_type,
                    value=value,
                )
            )
            identifier_owner_cache[cache_key] = project.id
            inserted_count += 1
    return inserted_count


def _create_unmatched_review_item(
    session: Session,
    *,
    source_run: SourceRun,
    raw_record: RawRecord,
    match_result: MatchResult,
    result: CollectPersistResult,
    create_new_candidates: bool,
) -> None:
    if _is_dismissed_source_record(
        session,
        source_name=raw_record.source_name,
        source_record_id=raw_record.source_record_id,
    ):
        if not match_result.candidate_project_ids:
            result.suppressed_new_candidate_records += 1
        return

    status_suggestion = _build_status_suggestion_for_unmatched_record(raw_record)
    if match_result.candidate_project_ids:
        item_type = ReviewItemType.POSSIBLE_MATCH
        priority = Priority.MEDIUM
        result.possible_match_review_items += 1
    else:
        if not create_new_candidates:
            result.suppressed_new_candidate_records += 1
            return
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
                "identifiers": _serialize_identifiers(raw_record.identifiers),
                "mapped_fields": _serialize_payload(raw_record.mapped_fields),
                "status_suggestion": _serialize_status_suggestion(status_suggestion),
                "raw_payload": _serialize_payload(raw_record.raw_payload),
                "source_row_id": raw_record.source_row_id,
                "source_created_at": serialize_json_value(raw_record.source_created_at),
                "source_updated_at": serialize_json_value(raw_record.source_updated_at),
                "source_row_hash": raw_record.source_row_hash,
            },
        )
    )


def _run_permit_agent_for_record(
    *,
    raw_record: RawRecord,
    match_result: MatchResult,
    triggers: tuple[AgentTrigger, ...],
    source_run: SourceRun,
    settings: Settings,
    session_factory: sessionmaker[Session],
    client: AgentClient | None,
    now: datetime,
    project_id: uuid.UUID | None = None,
    evidence_id: uuid.UUID | None = None,
    review_item_ids: list[uuid.UUID] | None = None,
) -> PermitAgentRunOutcome:
    if not triggers:
        return PermitAgentRunOutcome(client=client)
    resolved_client = client
    if settings.agent_enabled_for_permits and resolved_client is None:
        if settings.agent_allow_live_llm:
            resolved_client = build_anthropic_agent_client(
                settings=settings,
                profile=PERMIT_AGENT_PROFILE,
            )
    agent_result = run_agent_for_intake(
        IntakeRecord(
            source_type=PERMIT_AGENT_PROFILE.intake_source_type,
            intake_record_id=raw_record.source_record_id,
            project_id=project_id,
            source_run_id=source_run.id,
            payload=_permit_agent_intake_payload(
                raw_record,
                match_result=match_result,
                evidence_id=evidence_id,
            ),
        ),
        matcher_results=[_serialize_match_result(match_result)],
        trigger_reasons=list(triggers),
        profile=PERMIT_AGENT_PROFILE,
        client=resolved_client,
        produced_review_item_ids=review_item_ids or [],
        settings=settings,
        session_factory=session_factory,
        now=now,
    )
    # Permit integration is audit-only in this slice; deterministic review items stay authoritative.
    return PermitAgentRunOutcome(client=resolved_client, result=agent_result)


def _permit_agent_triggers_for_unmatched_record(
    *,
    raw_record: RawRecord,
    match_result: MatchResult,
    create_new_candidates: bool,
) -> tuple[AgentTrigger, ...]:
    if not _is_permit_agent_source(raw_record):
        return ()
    if (
        create_new_candidates
        and match_result.project_id is None
        and not match_result.candidate_project_ids
    ):
        return (AgentTrigger.NEW_CANDIDATE,)
    return ()


def _permit_agent_triggers_for_matched_record(
    *,
    previous_snapshot: ProjectDiffSnapshot,
    raw_record: RawRecord,
) -> tuple[AgentTrigger, ...]:
    if not _is_permit_agent_source(raw_record):
        return ()
    triggers: list[AgentTrigger] = []
    if _unit_delta_exceeds_threshold(
        previous_snapshot.total_units,
        _coerce_int(raw_record.mapped_fields.get("total_units")),
    ):
        triggers.append(AgentTrigger.UNIT_DELTA)
    # Real LADBS rows do not emit product_type until the deterministic LADBS
    # semantic port lands; this trigger is currently wired but inert in production.
    new_product_type = _coerce_product_type(raw_record.mapped_fields.get("product_type"))
    if new_product_type is not None and new_product_type != previous_snapshot.product_type:
        triggers.append(AgentTrigger.PRODUCT_TYPE_CHANGE)
    return tuple(triggers)


def _is_permit_agent_source(raw_record: RawRecord) -> bool:
    return get_logical_source_type(raw_record.source_name) in PERMIT_AGENT_SOURCE_TYPES


def _unit_delta_exceeds_threshold(current_units: int | None, new_units: int | None) -> bool:
    if current_units is None or new_units is None or current_units == new_units:
        return False
    denominator = max(abs(current_units), 1)
    return abs(new_units - current_units) / denominator > PERMIT_AGENT_UNIT_DELTA_THRESHOLD


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_product_type(value: Any) -> ProductType | None:
    if not value:
        return None
    if isinstance(value, ProductType):
        return value
    try:
        return ProductType(str(value))
    except ValueError:
        return None


def _permit_agent_intake_payload(
    raw_record: RawRecord,
    *,
    match_result: MatchResult,
    evidence_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "source_name": raw_record.source_name,
        "source_type": get_logical_source_type(raw_record.source_name),
        "source_record_id": raw_record.source_record_id,
        "source_row_id": raw_record.source_row_id,
        "canonical_address": raw_record.canonical_address,
        "project_name": raw_record.project_name,
        "identifiers": _serialize_identifiers(raw_record.identifiers),
        "mapped_fields": _serialize_payload(raw_record.mapped_fields),
        "raw_payload": _serialize_payload(raw_record.raw_payload),
        "lat": raw_record.lat,
        "lng": raw_record.lng,
        "source_created_at": serialize_json_value(raw_record.source_created_at),
        "source_updated_at": serialize_json_value(raw_record.source_updated_at),
        "source_row_hash": raw_record.source_row_hash,
        "evidence_id": str(evidence_id) if evidence_id is not None else None,
        "match": _serialize_match_result(match_result),
    }


def _review_item_ids_for_source_record(
    session: Session,
    *,
    source_run: SourceRun,
    raw_record: RawRecord,
) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(ReviewItem.id).where(
                ReviewItem.source_run_id == source_run.id,
                ReviewItem.payload["source_record_id"].astext == raw_record.source_record_id,
            )
        ).scalars()
    )


def _upsert_status_change_review_items(
    session: Session,
    *,
    project: Project,
    source_run: SourceRun,
    raw_record: RawRecord,
    match_result: MatchResult,
    diff_result: DiffResult,
    resolution_result,
    evidence_id: uuid.UUID | None,
) -> StatusReviewItemUpsertResult:
    base_payload = {
        "match": _serialize_match_result(match_result),
        "source_record_id": raw_record.source_record_id,
        "canonical_address": raw_record.canonical_address,
        "mapped_fields": _serialize_payload(raw_record.mapped_fields),
    }
    result = StatusReviewItemUpsertResult()
    for field_name in _review_item_fields(diff_result):
        field_changes = [
            change for change in diff_result.field_changes if change.field == field_name
        ]
        field_flags = _review_flags_for_field(diff_result.review_flags, field_name)
        payload = {
            **base_payload,
            "changes": [_serialize_change(change) for change in field_changes],
            "review_flags": [_serialize_review_flag(review_flag) for review_flag in field_flags],
            "status_suggestion": (
                _serialize_status_suggestion(diff_result.status_suggestion)
                if field_name == "pipeline_status"
                else None
            ),
            "current_value": serialize_json_value(getattr(project, field_name, None)),
        }
        proposed_value = proposed_value_for_payload(payload, field_name)
        if evidence_id is not None:
            payload["evidence_ids"] = [str(evidence_id)]
        winning_evidence_id = _winning_evidence_id_for_field(
            resolution_result,
            field_name,
            fallback=evidence_id,
        )
        item, _created = upsert_decision_card_review_item(
            session,
            project_id=project.id,
            source_run_id=source_run.id,
            item_type=ReviewItemType.STATUS_CHANGE,
            field_name=field_name,
            priority=_review_priority_for_field(diff_result, field_name),
            match_confidence=match_result.confidence,
            payload=payload,
            proposed_value=proposed_value,
            winning_evidence_id=winning_evidence_id,
        )
        result.review_item_ids.append(item.id)
        if _created:
            result.created_count += 1
    return result


def _review_item_fields(diff_result: DiffResult) -> list[str]:
    fields: list[str] = []
    if diff_result.status_suggestion is not None:
        fields.append("pipeline_status")
    fields.extend(change.field for change in diff_result.field_changes)
    fields.extend(_field_for_review_flag(review_flag) for review_flag in diff_result.review_flags)
    return _dedupe_text(field_name for field_name in fields if field_name)


def _dedupe_text(values) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _field_for_review_flag(review_flag: ReviewFlag) -> str:
    if review_flag.code in {
        "status_transition_requires_review",
        "permit_issued_requires_review",
    }:
        return "pipeline_status"
    if review_flag.code == "unit_split_mismatch":
        return "total_units"
    if review_flag.code in {
        "developer_canonicalization_review",
        "developer_registry_new_name",
    }:
        return "developer"
    return "pipeline_status"


def _review_flags_for_field(
    review_flags: list[ReviewFlag],
    field_name: str,
) -> list[ReviewFlag]:
    return [
        review_flag
        for review_flag in review_flags
        if _field_for_review_flag(review_flag) == field_name
    ]


def _review_priority_for_field(diff_result: DiffResult, field_name: str) -> Priority:
    field_flags = _review_flags_for_field(diff_result.review_flags, field_name)
    if any(review_flag.priority == Priority.HIGH for review_flag in field_flags):
        return Priority.HIGH
    if (
        field_name == "pipeline_status"
        and diff_result.status_suggestion is not None
        and diff_result.status_suggestion.priority == Priority.HIGH
    ):
        return Priority.HIGH
    if any(
        change.field == field_name and change.priority == Priority.HIGH
        for change in diff_result.field_changes
    ):
        return Priority.HIGH
    return Priority.MEDIUM


def _winning_evidence_id_for_field(
    resolution_result,
    field_name: str,
    *,
    fallback: uuid.UUID | None,
) -> uuid.UUID | None:
    if resolution_result is None:
        return fallback
    field_resolution = resolution_result.field_resolutions.get(field_name)
    if field_resolution is None or not field_resolution.evidence_ids:
        return fallback
    return field_resolution.evidence_ids[0]


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


def _serialize_identifiers(identifiers: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        str(key): [str(value) for value in values if value]
        for key, values in identifiers.items()
        if values
    }


def _serialize_review_flag(review_flag: ReviewFlag) -> dict[str, Any]:
    return {
        "code": review_flag.code,
        "message": review_flag.message,
        "priority": review_flag.priority.value,
    }


def _serialize_status_suggestion(
    suggestion: StatusSuggestion | None,
) -> dict[str, Any] | None:
    if suggestion is None:
        return None
    return {
        "current_status": (
            suggestion.current_status.value if suggestion.current_status is not None else None
        ),
        "suggested_status": suggestion.suggested_status.value,
        "evidence_type": suggestion.evidence_type,
        "evidence_date": serialize_json_value(suggestion.evidence_date),
        "reason": suggestion.reason,
        "priority": suggestion.priority.value,
        "rule_code": suggestion.rule_code,
        "proof_level": suggestion.proof_level,
    }


def _build_status_suggestion_for_unmatched_record(
    raw_record: RawRecord,
) -> StatusSuggestion | None:
    evidence_type = _coerce_text(raw_record.mapped_fields.get("status_evidence_type"))
    evidence_date = _parse_date(raw_record.mapped_fields.get("status_evidence_date"))
    reason = _coerce_text(raw_record.mapped_fields.get("status_evidence_reason"))
    return build_status_suggestion(
        current_status=None,
        evidence_type=evidence_type,
        evidence_date=evidence_date,
        reason_override=reason,
    )


def _status_evidence_type_from_resolution(resolution_result) -> str | None:
    if resolution_result is None:
        return None
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    metadata = status_resolution.metadata or {}
    evidence_type = metadata.get("evidence_type")
    if evidence_type is None:
        return None
    text = str(evidence_type).strip()
    return text or None


def _status_evidence_date_from_resolution(resolution_result) -> date | None:
    if resolution_result is None:
        return None
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    return status_resolution.evidence_date


def _status_reason_from_resolution(resolution_result) -> str | None:
    if resolution_result is None:
        return None
    status_resolution = resolution_result.field_resolutions.get("pipeline_status")
    if status_resolution is None:
        return None
    review_reason = status_resolution.metadata.get("review_reason")
    if review_reason is None:
        return None
    text = str(review_reason).strip()
    return text or None


def _review_flags_from_resolution(resolution_result) -> list[ReviewFlag]:
    if resolution_result is None:
        return []
    return list(resolution_result.review_flags)


def _serialize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_json_value(value) for key, value in payload.items()}


def _source_updated_at_bounds(
    raw_records: list[RawRecord],
) -> tuple[datetime | None, datetime | None]:
    updated_at_values = sorted(
        raw_record.source_updated_at
        for raw_record in raw_records
        if raw_record.source_updated_at is not None
    )
    if not updated_at_values:
        return None, None
    return updated_at_values[0], updated_at_values[-1]


def _is_dismissed_source_record(
    session: Session,
    *,
    source_name: str,
    source_record_id: str,
) -> bool:
    dismissed_record = session.execute(
        select(DismissedRecord.id).where(
            DismissedRecord.source == source_name,
            DismissedRecord.source_record_id == source_record_id,
        )
    ).scalar_one_or_none()
    return dismissed_record is not None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
