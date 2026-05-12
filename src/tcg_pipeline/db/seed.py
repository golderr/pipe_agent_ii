from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.evidence import (
    write_pipedream_snapshot_evidence,
    write_source_record_evidence,
)
from tcg_pipeline.db.models import (
    AgeRestriction,
    DismissedRecord,
    GeocodeConfidence,
    IdentifierType,
    ProductType,
    Project,
    ProjectIdentifier,
    ProjectRelationship,
    ProjectSourceRecord,
    RelationshipType,
    RentOrSale,
    StatusHistory,
)
from tcg_pipeline.db.status_regression_reviews import (
    status_regression_candidates_for_evidence,
    upsert_structured_status_regression_review_items,
)
from tcg_pipeline.ingesters.costar import (
    COSTAR_SOURCE_NAME,
    CoStarImportResult,
    CoStarIngester,
    CoStarProjectRecord,
)
from tcg_pipeline.ingesters.pipedream import (
    PIPEDREAM_SOURCE_NAME,
    PipedreamImportResult,
    PipedreamIngester,
    PipedreamProjectRecord,
    StagedProjectRelationship,
)
from tcg_pipeline.resolution import resolve_project

logger = logging.getLogger(__name__)

ADDRESS_MATCH = "address"
APN_MATCH = "apn"
COSTAR_PROPERTY_ID_MATCH = "costar_property_id"


@dataclass(slots=True)
class UnresolvedPipedreamRelationship:
    project_identifier_value: str
    related_project_identifier_value: str
    relationship_type: RelationshipType
    source_field: str
    missing_identifiers: list[str]


@dataclass(slots=True)
class PipedreamPersistResult:
    inserted_projects: int = 0
    inserted_dismissed_records: int = 0
    created_relationships: int = 0
    skipped_existing_relationships: int = 0
    skipped_existing_project_ids: list[str] = field(default_factory=list)
    skipped_existing_dismissed_record_ids: list[str] = field(default_factory=list)
    unresolved_relationships: list[UnresolvedPipedreamRelationship] = field(default_factory=list)

    @property
    def skipped_existing_project_count(self) -> int:
        return len(self.skipped_existing_project_ids)

    @property
    def skipped_existing_dismissed_count(self) -> int:
        return len(self.skipped_existing_dismissed_record_ids)

    @property
    def unresolved_relationship_count(self) -> int:
        return len(self.unresolved_relationships)


@dataclass(slots=True)
class AmbiguousCoStarMatch:
    property_id: str
    candidate_project_ids: list[uuid.UUID]
    match_strategy: str


@dataclass(slots=True)
class CoStarPersistResult:
    inserted_projects: int = 0
    matched_existing_projects: int = 0
    matched_by_costar_property_id: int = 0
    matched_by_apn: int = 0
    matched_by_address: int = 0
    inserted_identifiers: int = 0
    skipped_existing_identifiers: int = 0
    inserted_source_records: int = 0
    updated_source_records: int = 0
    inserted_status_history_entries: int = 0
    skipped_existing_status_history_entries: int = 0
    merged_fields: int = 0
    status_regression_review_items: int = 0
    status_regression_review_item_ids: list[uuid.UUID] = field(default_factory=list)
    ambiguous_matches: list[AmbiguousCoStarMatch] = field(default_factory=list)

    @property
    def ambiguous_match_count(self) -> int:
        return len(self.ambiguous_matches)


def ingest_pipedream_workbooks(
    workbook_paths: Sequence[str | Path],
    *,
    market: str,
    source_name: str = PIPEDREAM_SOURCE_NAME,
    allowed_cities: Iterable[str] | None = None,
) -> list[PipedreamImportResult]:
    ingester = PipedreamIngester(
        market=market,
        source_name=source_name,
        allowed_cities=allowed_cities,
    )
    return [ingester.ingest_workbook(path) for path in workbook_paths]


def ingest_costar_workbooks(
    workbook_paths: Sequence[str | Path],
    *,
    market: str,
    source_name: str = COSTAR_SOURCE_NAME,
    allowed_cities: Iterable[str] | None = None,
) -> CoStarImportResult:
    ingester = CoStarIngester(
        market=market,
        source_name=source_name,
        allowed_cities=allowed_cities,
    )
    return ingester.ingest_workbooks(workbook_paths)


def persist_pipedream_import_result(
    session: Session,
    import_result: PipedreamImportResult,
) -> PipedreamPersistResult:
    return persist_pipedream_import_results(session, [import_result])


def persist_pipedream_import_results(
    session: Session,
    import_results: Sequence[PipedreamImportResult],
) -> PipedreamPersistResult:
    project_records = [
        project_record
        for import_result in import_results
        for project_record in import_result.project_records
    ]
    dismissed_records = [
        dismissed_record
        for import_result in import_results
        for dismissed_record in import_result.dismissed_records
    ]
    staged_relationships = [
        staged_relationship
        for import_result in import_results
        for staged_relationship in import_result.staged_relationships
    ]

    existing_project_ids = _load_existing_identifier_values(
        session,
        identifier_type=IdentifierType.TCG_PIPEDREAM_ID,
        values=[record.project_identifier_value for record in project_records],
    )
    pending_project_ids = set(existing_project_ids)
    projects_to_insert: list[PipedreamProjectRecord] = []
    persist_result = PipedreamPersistResult()

    for project_record in project_records:
        if project_record.project_identifier_value in pending_project_ids:
            persist_result.skipped_existing_project_ids.append(project_record.project_identifier_value)
            continue
        pending_project_ids.add(project_record.project_identifier_value)
        projects_to_insert.append(project_record)

    existing_dismissed_keys = _load_existing_dismissed_keys(
        session,
        dismissed_records=dismissed_records,
    )
    pending_dismissed_keys = set(existing_dismissed_keys)
    dismissed_to_insert: list[DismissedRecord] = []

    for dismissed_record in dismissed_records:
        dismissed_key = (dismissed_record.source, dismissed_record.source_record_id)
        if dismissed_key in pending_dismissed_keys:
            persist_result.skipped_existing_dismissed_record_ids.append(
                dismissed_record.source_record_id
            )
            continue
        pending_dismissed_keys.add(dismissed_key)
        dismissed_to_insert.append(dismissed_record)

    session.add_all([project_record.project for project_record in projects_to_insert])
    session.add_all(
        [
            project_note
            for project_record in projects_to_insert
            for project_note in project_record.project_notes
        ]
    )
    session.add_all(dismissed_to_insert)
    session.flush()

    persist_result.inserted_projects = len(projects_to_insert)
    persist_result.inserted_dismissed_records = len(dismissed_to_insert)
    for project_record in projects_to_insert:
        evidence_result = write_pipedream_snapshot_evidence(
            session,
            project=project_record.project,
            source_record=project_record.source_record,
            ingest_method="seed_import",
            notes="Captured from pipedream seed import.",
        )
        if evidence_result.changed:
            session.flush()
            resolve_project(
                project_record.project.id,
                session,
                apply=True,
                write_resolution_log=True,
            )
    _persist_staged_relationships(session, staged_relationships, persist_result)
    return persist_result


def persist_costar_import_result(
    session: Session,
    import_result: CoStarImportResult,
) -> CoStarPersistResult:
    return persist_costar_import_results(session, [import_result])


def persist_costar_import_results(
    session: Session,
    import_results: Sequence[CoStarImportResult],
) -> CoStarPersistResult:
    project_records = [
        project_record
        for import_result in import_results
        for project_record in import_result.project_records
    ]
    if not project_records:
        return CoStarPersistResult()

    property_ids = [record.property_id for record in project_records]
    apn_values = [
        identifier.value
        for record in project_records
        for identifier in record.identifiers
        if identifier.identifier_type == IdentifierType.APN
    ]
    markets = {record.project.market for record in project_records}
    addresses = {record.project.canonical_address for record in project_records}
    source_record_keys = {
        (record.source_record.source_name, record.source_record.source_record_id)
        for record in project_records
    }

    costar_property_map, apn_map = _load_costar_identifier_maps(
        session,
        property_ids=property_ids,
        apn_values=apn_values,
    )
    address_map = _load_address_map(session, markets=markets, addresses=addresses)
    source_record_map = _load_source_record_map(session, source_record_keys=source_record_keys)
    identifier_cache: dict[uuid.UUID, set[tuple[IdentifierType, str]]] = {}
    status_history_cache: dict[uuid.UUID, set[tuple[Any, ...]]] = {}
    project_cache: dict[uuid.UUID, Project] = {}
    persist_result = CoStarPersistResult()

    for project_record in project_records:
        matched_project_id, match_type, candidate_project_ids = _match_costar_project(
            project_record=project_record,
            costar_property_map=costar_property_map,
            apn_map=apn_map,
            address_map=address_map,
        )
        if candidate_project_ids:
            persist_result.ambiguous_matches.append(
                AmbiguousCoStarMatch(
                    property_id=project_record.property_id,
                    candidate_project_ids=candidate_project_ids,
                    match_strategy=match_type or APN_MATCH,
                )
            )
            logger.warning(
                "CoStar property %s has ambiguous %s candidates=%s; skipping",
                project_record.property_id,
                match_type or APN_MATCH,
                candidate_project_ids,
            )
            continue

        if matched_project_id is None:
            _persist_new_costar_project(
                session,
                project_record=project_record,
                persist_result=persist_result,
                project_cache=project_cache,
                costar_property_map=costar_property_map,
                apn_map=apn_map,
                address_map=address_map,
                source_record_map=source_record_map,
                identifier_cache=identifier_cache,
                status_history_cache=status_history_cache,
            )
            target_project_id = project_record.project.id
            target_project = project_record.project
        else:
            existing_project = _load_costar_project(
                session,
                project_id=matched_project_id,
                project_cache=project_cache,
            )
            if existing_project is None:
                logger.warning(
                    "Matched CoStar property %s to missing project id %s; skipping",
                    project_record.property_id,
                    matched_project_id,
                )
                continue

            _increment_costar_match_counter(persist_result, match_type)
            _merge_costar_project(
                session,
                existing_project=existing_project,
                project_record=project_record,
                persist_result=persist_result,
                source_record_map=source_record_map,
                identifier_cache=identifier_cache,
                status_history_cache=status_history_cache,
                costar_property_map=costar_property_map,
                apn_map=apn_map,
                address_map=address_map,
            )
            target_project_id = existing_project.id
            target_project = existing_project

        evidence_result = write_source_record_evidence(
            session,
            project_id=target_project_id,
            source_record=project_record.source_record,
            ingest_method="seed_import",
            notes="Captured from costar seed import.",
        )
        if evidence_result.changed:
            session.flush()
            resolution_result = resolve_project(
                target_project_id,
                session,
                apply=True,
                write_resolution_log=True,
            )
            status_regression_candidates = status_regression_candidates_for_evidence(
                resolution_result,
                source_name=project_record.source_record.source_name,
                evidence_id=evidence_result.evidence.id if evidence_result.evidence else None,
            )
            status_regression_result = upsert_structured_status_regression_review_items(
                session,
                project=target_project,
                source_name=project_record.source_record.source_name,
                source_record_id=project_record.source_record.source_record_id,
                mapped_fields=project_record.source_record.mapped_fields,
                candidates=status_regression_candidates,
            )
            persist_result.status_regression_review_items += (
                status_regression_result.created_count
            )
            persist_result.status_regression_review_item_ids.extend(
                status_regression_result.review_item_ids
            )

    session.flush()
    return persist_result


def seed_pipedream_workbooks(
    session: Session,
    workbook_paths: Sequence[str | Path],
    *,
    market: str,
    source_name: str = PIPEDREAM_SOURCE_NAME,
    allowed_cities: Iterable[str] | None = None,
) -> tuple[list[PipedreamImportResult], PipedreamPersistResult]:
    import_results = ingest_pipedream_workbooks(
        workbook_paths,
        market=market,
        source_name=source_name,
        allowed_cities=allowed_cities,
    )
    persist_result = persist_pipedream_import_results(session, import_results)
    return import_results, persist_result


def seed_costar_workbooks(
    session: Session,
    workbook_paths: Sequence[str | Path],
    *,
    market: str,
    source_name: str = COSTAR_SOURCE_NAME,
    allowed_cities: Iterable[str] | None = None,
) -> tuple[CoStarImportResult, CoStarPersistResult]:
    import_result = ingest_costar_workbooks(
        workbook_paths,
        market=market,
        source_name=source_name,
        allowed_cities=allowed_cities,
    )
    persist_result = persist_costar_import_result(session, import_result)
    return import_result, persist_result


def _load_existing_identifier_values(
    session: Session,
    *,
    identifier_type: IdentifierType,
    values: Sequence[str],
) -> set[str]:
    normalized_values = {value for value in values if value}
    if not normalized_values:
        return set()

    rows = session.execute(
        select(ProjectIdentifier.value).where(
            ProjectIdentifier.identifier_type == identifier_type,
            ProjectIdentifier.value.in_(normalized_values),
        )
    ).all()
    return {row.value for row in rows}


def _load_existing_dismissed_keys(
    session: Session,
    *,
    dismissed_records: Sequence[DismissedRecord],
) -> set[tuple[str, str]]:
    keys = {
        (dismissed_record.source, dismissed_record.source_record_id)
        for dismissed_record in dismissed_records
    }
    if not keys:
        return set()

    sources = {source for source, _ in keys}
    source_record_ids = {source_record_id for _, source_record_id in keys}
    rows = session.execute(
        select(DismissedRecord.source, DismissedRecord.source_record_id).where(
            DismissedRecord.source.in_(sources),
            DismissedRecord.source_record_id.in_(source_record_ids),
        )
    ).all()
    return {
        (row.source, row.source_record_id)
        for row in rows
        if (row.source, row.source_record_id) in keys
    }


def _load_costar_identifier_maps(
    session: Session,
    *,
    property_ids: Sequence[str],
    apn_values: Sequence[str],
) -> tuple[dict[str, uuid.UUID], dict[str, set[uuid.UUID]]]:
    property_id_values = {value for value in property_ids if value}
    apn_identifier_values = {value for value in apn_values if value}
    if not property_id_values and not apn_identifier_values:
        return {}, {}

    identifier_filters = []
    if property_id_values:
        identifier_filters.append(
            and_(
                ProjectIdentifier.identifier_type == IdentifierType.COSTAR_PROPERTY_ID,
                ProjectIdentifier.value.in_(property_id_values),
            )
        )
    if apn_identifier_values:
        identifier_filters.append(
            and_(
                ProjectIdentifier.identifier_type == IdentifierType.APN,
                ProjectIdentifier.value.in_(apn_identifier_values),
            )
        )

    rows = session.execute(
        select(
            ProjectIdentifier.identifier_type,
            ProjectIdentifier.value,
            ProjectIdentifier.project_id,
        ).where(or_(*identifier_filters))
    ).all()

    costar_property_map: dict[str, uuid.UUID] = {}
    apn_map: dict[str, set[uuid.UUID]] = defaultdict(set)
    for row in rows:
        if row.identifier_type == IdentifierType.COSTAR_PROPERTY_ID:
            costar_property_map[row.value] = row.project_id
            continue
        apn_map[row.value].add(row.project_id)
    return costar_property_map, apn_map


def _load_address_map(
    session: Session,
    *,
    markets: set[str],
    addresses: set[str],
) -> dict[tuple[str, str], set[uuid.UUID]]:
    if not markets or not addresses:
        return {}

    rows = session.execute(
        select(Project.id, Project.market, Project.canonical_address).where(
            Project.market.in_(markets),
            Project.canonical_address.in_(addresses),
        )
    ).all()

    address_map: dict[tuple[str, str], set[uuid.UUID]] = defaultdict(set)
    for row in rows:
        address_map[(row.market, row.canonical_address)].add(row.id)
    return address_map


def _load_source_record_map(
    session: Session,
    *,
    source_record_keys: set[tuple[str, str]],
) -> dict[tuple[str, str], ProjectSourceRecord]:
    if not source_record_keys:
        return {}

    source_names = {source_name for source_name, _ in source_record_keys}
    source_record_ids = {source_record_id for _, source_record_id in source_record_keys}
    source_records = session.execute(
        select(ProjectSourceRecord).where(
            ProjectSourceRecord.source_name.in_(source_names),
            ProjectSourceRecord.source_record_id.in_(source_record_ids),
        )
    ).scalars()

    return {
        (source_record.source_name, source_record.source_record_id): source_record
        for source_record in source_records
        if (source_record.source_name, source_record.source_record_id) in source_record_keys
    }


def _match_costar_project(
    *,
    project_record: CoStarProjectRecord,
    costar_property_map: dict[str, uuid.UUID],
    apn_map: dict[str, set[uuid.UUID]],
    address_map: dict[tuple[str, str], set[uuid.UUID]],
) -> tuple[uuid.UUID | None, str | None, list[uuid.UUID] | None]:
    property_id_match = costar_property_map.get(project_record.property_id)
    if property_id_match is not None:
        return property_id_match, COSTAR_PROPERTY_ID_MATCH, None

    apn_matches: set[uuid.UUID] = set()
    for identifier in project_record.identifiers:
        if identifier.identifier_type != IdentifierType.APN:
            continue
        apn_matches.update(apn_map.get(identifier.value, set()))

    if len(apn_matches) == 1:
        return next(iter(apn_matches)), APN_MATCH, None
    if len(apn_matches) > 1:
        address_candidates = address_map.get(
            (project_record.project.market, project_record.project.canonical_address),
            set(),
        )
        narrowed_candidates = sorted(apn_matches & address_candidates)
        if len(narrowed_candidates) == 1:
            return narrowed_candidates[0], APN_MATCH, None
        return None, APN_MATCH, sorted(apn_matches)

    address_matches = address_map.get(
        (project_record.project.market, project_record.project.canonical_address),
        set(),
    )
    if len(address_matches) == 1:
        return next(iter(address_matches)), ADDRESS_MATCH, None
    if len(address_matches) > 1:
        return None, ADDRESS_MATCH, sorted(address_matches)

    return None, None, None


def _persist_new_costar_project(
    session: Session,
    *,
    project_record: CoStarProjectRecord,
    persist_result: CoStarPersistResult,
    project_cache: dict[uuid.UUID, Project],
    costar_property_map: dict[str, uuid.UUID],
    apn_map: dict[str, set[uuid.UUID]],
    address_map: dict[tuple[str, str], set[uuid.UUID]],
    source_record_map: dict[tuple[str, str], ProjectSourceRecord],
    identifier_cache: dict[uuid.UUID, set[tuple[IdentifierType, str]]],
    status_history_cache: dict[uuid.UUID, set[tuple[Any, ...]]],
) -> None:
    project_id = _ensure_project_id(project_record.project)
    session.add(project_record.project)

    persist_result.inserted_projects += 1
    persist_result.inserted_identifiers += len(project_record.identifiers)
    persist_result.inserted_source_records += 1
    persist_result.inserted_status_history_entries += len(project_record.status_history)

    _register_project_maps(
        project_record=project_record,
        project_id=project_id,
        project_cache=project_cache,
        costar_property_map=costar_property_map,
        apn_map=apn_map,
        address_map=address_map,
        source_record_map=source_record_map,
        identifier_cache=identifier_cache,
        status_history_cache=status_history_cache,
    )


def _merge_costar_project(
    session: Session,
    *,
    existing_project: Project,
    project_record: CoStarProjectRecord,
    persist_result: CoStarPersistResult,
    source_record_map: dict[tuple[str, str], ProjectSourceRecord],
    identifier_cache: dict[uuid.UUID, set[tuple[IdentifierType, str]]],
    status_history_cache: dict[uuid.UUID, set[tuple[Any, ...]]],
    costar_property_map: dict[str, uuid.UUID],
    apn_map: dict[str, set[uuid.UUID]],
    address_map: dict[tuple[str, str], set[uuid.UUID]],
) -> None:
    persist_result.matched_existing_projects += 1
    persist_result.merged_fields += _merge_project_fields(
        existing_project=existing_project,
        incoming_project=project_record.project,
    )

    _merge_identifiers(
        session,
        project_id=existing_project.id,
        property_id=project_record.property_id,
        identifiers=project_record.identifiers,
        persist_result=persist_result,
        identifier_cache=identifier_cache,
        costar_property_map=costar_property_map,
        apn_map=apn_map,
    )
    _upsert_source_record(
        session,
        project_id=existing_project.id,
        source_record=project_record.source_record,
        persist_result=persist_result,
        source_record_map=source_record_map,
    )
    _merge_status_history(
        session,
        project_id=existing_project.id,
        status_history_entries=project_record.status_history,
        persist_result=persist_result,
        status_history_cache=status_history_cache,
    )

    address_key = (existing_project.market, existing_project.canonical_address)
    address_map[address_key].add(existing_project.id)


def _merge_project_fields(
    *,
    existing_project: Project,
    incoming_project: Project,
) -> int:
    merged_fields = 0

    merged_fields += _merge_string_list_field(
        existing_project=existing_project,
        field_name="raw_addresses",
        incoming_values=incoming_project.raw_addresses,
    )

    if existing_project.lat is None and incoming_project.lat is not None:
        existing_project.lat = incoming_project.lat
        merged_fields += 1
    if existing_project.lng is None and incoming_project.lng is not None:
        existing_project.lng = incoming_project.lng
        merged_fields += 1
    if existing_project.location is None and incoming_project.location is not None:
        existing_project.location = incoming_project.location
        merged_fields += 1
    if (
        existing_project.geocode_confidence == GeocodeConfidence.NONE
        and incoming_project.geocode_confidence != GeocodeConfidence.NONE
    ):
        existing_project.geocode_confidence = incoming_project.geocode_confidence
        merged_fields += 1

    # CoStar only carries total units, not separate market-rate or affordable unit counts.
    # Keep those Pipedream-specific fields untouched until a future source provides them.
    for field_name in (
        "city",
        "state",
        "county",
        "zip",
        "costar_submarket",
        "zoning",
        "project_name",
        "developer",
        "stories",
        "total_units",
        "pct_studio",
        "pct_1bed",
        "pct_2bed",
        "pct_other_bed",
        "acres",
        "hotel_keys",
        "total_sf",
        "parking_spaces",
        "style",
        "property_type",
        "affordable_type",
        "owner",
        "true_owner",
        "architect",
        "date_delivery",
        "date_construction_start",
    ):
        merged_fields += _fill_if_missing(
            existing_project=existing_project,
            field_name=field_name,
            incoming_value=getattr(incoming_project, field_name),
        )

    merged_fields += _fill_enum_if_unknown(
        existing_project=existing_project,
        field_name="rent_or_sale",
        incoming_value=incoming_project.rent_or_sale,
        unknown_value=RentOrSale.UNKNOWN,
    )
    merged_fields += _fill_enum_if_unknown(
        existing_project=existing_project,
        field_name="product_type",
        incoming_value=incoming_project.product_type,
        unknown_value=ProductType.UNKNOWN,
    )
    merged_fields += _fill_enum_if_unknown(
        existing_project=existing_project,
        field_name="age_restriction",
        incoming_value=incoming_project.age_restriction,
        unknown_value=AgeRestriction.UNKNOWN,
    )

    return merged_fields


def _fill_if_missing(
    *,
    existing_project: Project,
    field_name: str,
    incoming_value: Any,
) -> int:
    if _is_blank_scalar(incoming_value):
        return 0

    existing_value = getattr(existing_project, field_name)
    if not _is_blank_scalar(existing_value):
        return 0

    setattr(existing_project, field_name, incoming_value)
    return 1


def _fill_enum_if_unknown(
    *,
    existing_project: Project,
    field_name: str,
    incoming_value: Any,
    unknown_value: Any,
) -> int:
    if incoming_value in (None, unknown_value):
        return 0
    if getattr(existing_project, field_name) != unknown_value:
        return 0
    setattr(existing_project, field_name, incoming_value)
    return 1


def _merge_string_list_field(
    *,
    existing_project: Project,
    field_name: str,
    incoming_values: Sequence[str],
) -> int:
    if not incoming_values:
        return 0

    existing_values = list(getattr(existing_project, field_name))
    merged = list(existing_values)
    changed = False
    for value in incoming_values:
        if not value or value in merged:
            continue
        merged.append(value)
        changed = True

    if changed:
        setattr(existing_project, field_name, merged)
        return 1
    return 0


def _merge_identifiers(
    session: Session,
    *,
    project_id: uuid.UUID,
    property_id: str,
    identifiers: Sequence[ProjectIdentifier],
    persist_result: CoStarPersistResult,
    identifier_cache: dict[uuid.UUID, set[tuple[IdentifierType, str]]],
    costar_property_map: dict[str, uuid.UUID],
    apn_map: dict[str, set[uuid.UUID]],
) -> None:
    existing_keys = _load_project_identifier_keys(
        session,
        project_id=project_id,
        identifier_cache=identifier_cache,
    )

    for identifier in identifiers:
        identifier_key = (identifier.identifier_type, identifier.value)
        if identifier_key in existing_keys:
            persist_result.skipped_existing_identifiers += 1
            continue
        if identifier.identifier_type == IdentifierType.COSTAR_PROPERTY_ID:
            owner_project_id = costar_property_map.get(identifier.value)
            if owner_project_id is not None and owner_project_id != project_id:
                logger.warning(
                    (
                        "Skipping conflicting CoStar property id %s for property %s: "
                        "already attached to %s, not %s"
                    ),
                    identifier.value,
                    property_id,
                    owner_project_id,
                    project_id,
                )
                persist_result.skipped_existing_identifiers += 1
                continue
        if identifier.identifier_type == IdentifierType.APN:
            owner_project_ids = apn_map.get(identifier.value, set())
            conflicting_owner_ids = {
                owner_project_id
                for owner_project_id in owner_project_ids
                if owner_project_id != project_id
            }
            if conflicting_owner_ids:
                logger.warning(
                    (
                        "Skipping conflicting APN %s for CoStar property %s: "
                        "already attached to %s, not %s"
                    ),
                    identifier.value,
                    property_id,
                    sorted(conflicting_owner_ids),
                    project_id,
                )
                persist_result.skipped_existing_identifiers += 1
                continue

        session.add(
            ProjectIdentifier(
                project_id=project_id,
                identifier_type=identifier.identifier_type,
                value=identifier.value,
                source=identifier.source,
                is_primary=identifier.is_primary,
                first_seen_at=identifier.first_seen_at,
                last_seen_at=identifier.last_seen_at,
                notes=identifier.notes,
            )
        )
        existing_keys.add(identifier_key)
        persist_result.inserted_identifiers += 1

        if identifier.identifier_type == IdentifierType.COSTAR_PROPERTY_ID:
            costar_property_map[identifier.value] = project_id
            continue
        if identifier.identifier_type == IdentifierType.APN:
            apn_map[identifier.value].add(project_id)


def _upsert_source_record(
    session: Session,
    *,
    project_id: uuid.UUID,
    source_record: ProjectSourceRecord,
    persist_result: CoStarPersistResult,
    source_record_map: dict[tuple[str, str], ProjectSourceRecord],
) -> None:
    source_record_key = (source_record.source_name, source_record.source_record_id)
    existing_source_record = source_record_map.get(source_record_key)
    if existing_source_record is None:
        new_source_record = ProjectSourceRecord(
            project_id=project_id,
            source_name=source_record.source_name,
            source_record_id=source_record.source_record_id,
            source_url=source_record.source_url,
            first_seen_at=source_record.first_seen_at,
            last_seen_at=source_record.last_seen_at,
            last_pulled_at=source_record.last_pulled_at,
            raw_payload=source_record.raw_payload,
            mapped_fields=source_record.mapped_fields,
            field_provenance=source_record.field_provenance,
        )
        session.add(new_source_record)
        source_record_map[source_record_key] = new_source_record
        persist_result.inserted_source_records += 1
        return

    if existing_source_record.project_id != project_id:
        logger.warning(
            "Source record conflict for %s:%s already attached to %s, not %s",
            source_record.source_name,
            source_record.source_record_id,
            existing_source_record.project_id,
            project_id,
        )
        return

    if existing_source_record.first_seen_at is None:
        existing_source_record.first_seen_at = source_record.first_seen_at
    existing_source_record.last_seen_at = source_record.last_seen_at
    existing_source_record.last_pulled_at = source_record.last_pulled_at
    existing_source_record.raw_payload = source_record.raw_payload
    existing_source_record.mapped_fields = source_record.mapped_fields
    existing_source_record.field_provenance = source_record.field_provenance
    if source_record.source_url and not existing_source_record.source_url:
        existing_source_record.source_url = source_record.source_url
    persist_result.updated_source_records += 1


def _merge_status_history(
    session: Session,
    *,
    project_id: uuid.UUID,
    status_history_entries: Sequence[StatusHistory],
    persist_result: CoStarPersistResult,
    status_history_cache: dict[uuid.UUID, set[tuple[Any, ...]]],
) -> None:
    existing_status_keys = _load_status_history_keys(
        session,
        project_id=project_id,
        status_history_cache=status_history_cache,
    )
    for status_history_entry in status_history_entries:
        status_key = (
            status_history_entry.status,
            status_history_entry.status_date,
            status_history_entry.source,
            status_history_entry.notes,
        )
        if status_key in existing_status_keys:
            persist_result.skipped_existing_status_history_entries += 1
            continue

        session.add(
            StatusHistory(
                project_id=project_id,
                status=status_history_entry.status,
                status_date=status_history_entry.status_date,
                source=status_history_entry.source,
                notes=status_history_entry.notes,
            )
        )
        existing_status_keys.add(status_key)
        persist_result.inserted_status_history_entries += 1


def _load_project_identifier_keys(
    session: Session,
    *,
    project_id: uuid.UUID,
    identifier_cache: dict[uuid.UUID, set[tuple[IdentifierType, str]]],
) -> set[tuple[IdentifierType, str]]:
    cached_identifier_keys = identifier_cache.get(project_id)
    if cached_identifier_keys is not None:
        return cached_identifier_keys

    rows = session.execute(
        select(ProjectIdentifier.identifier_type, ProjectIdentifier.value).where(
            ProjectIdentifier.project_id == project_id
        )
    ).all()
    identifier_cache[project_id] = {
        (row.identifier_type, row.value)
        for row in rows
    }
    return identifier_cache[project_id]


def _load_status_history_keys(
    session: Session,
    *,
    project_id: uuid.UUID,
    status_history_cache: dict[uuid.UUID, set[tuple[Any, ...]]],
) -> set[tuple[Any, ...]]:
    cached_status_keys = status_history_cache.get(project_id)
    if cached_status_keys is not None:
        return cached_status_keys

    rows = session.execute(
        select(
            StatusHistory.status,
            StatusHistory.status_date,
            StatusHistory.source,
            StatusHistory.notes,
        ).where(StatusHistory.project_id == project_id)
    ).all()
    status_history_cache[project_id] = {
        (row.status, row.status_date, row.source, row.notes)
        for row in rows
    }
    return status_history_cache[project_id]


def _register_project_maps(
    *,
    project_record: CoStarProjectRecord,
    project_id: uuid.UUID,
    project_cache: dict[uuid.UUID, Project],
    costar_property_map: dict[str, uuid.UUID],
    apn_map: dict[str, set[uuid.UUID]],
    address_map: dict[tuple[str, str], set[uuid.UUID]],
    source_record_map: dict[tuple[str, str], ProjectSourceRecord],
    identifier_cache: dict[uuid.UUID, set[tuple[IdentifierType, str]]],
    status_history_cache: dict[uuid.UUID, set[tuple[Any, ...]]],
) -> None:
    project_cache[project_id] = project_record.project
    address_key = (
        project_record.project.market,
        project_record.project.canonical_address,
    )
    address_map[address_key].add(project_id)
    source_record_map[
        (project_record.source_record.source_name, project_record.source_record.source_record_id)
    ] = project_record.source_record
    identifier_cache[project_id] = set()
    for identifier in project_record.identifiers:
        identifier_cache[project_id].add((identifier.identifier_type, identifier.value))
        if identifier.identifier_type == IdentifierType.COSTAR_PROPERTY_ID:
            costar_property_map[identifier.value] = project_id
            continue
        if identifier.identifier_type == IdentifierType.APN:
            apn_map[identifier.value].add(project_id)

    status_history_cache[project_id] = {
        (status.status, status.status_date, status.source, status.notes)
        for status in project_record.status_history
    }


def _load_costar_project(
    session: Session,
    *,
    project_id: uuid.UUID,
    project_cache: dict[uuid.UUID, Project],
) -> Project | None:
    cached_project = project_cache.get(project_id)
    if cached_project is not None:
        return cached_project

    project = session.get(Project, project_id)
    if project is not None:
        project_cache[project_id] = project
    return project


def _increment_costar_match_counter(
    persist_result: CoStarPersistResult,
    match_type: str | None,
) -> None:
    if match_type == COSTAR_PROPERTY_ID_MATCH:
        persist_result.matched_by_costar_property_id += 1
        return
    if match_type == APN_MATCH:
        persist_result.matched_by_apn += 1
        return
    if match_type == ADDRESS_MATCH:
        persist_result.matched_by_address += 1


def _ensure_project_id(project: Project) -> uuid.UUID:
    if project.id is None:
        project.id = uuid.uuid4()
    return project.id


def _is_blank_scalar(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _persist_staged_relationships(
    session: Session,
    staged_relationships: Sequence[StagedProjectRelationship],
    persist_result: PipedreamPersistResult,
) -> None:
    if not staged_relationships:
        return

    identifier_values = {
        staged_relationship.project_identifier_value
        for staged_relationship in staged_relationships
    } | {
        staged_relationship.related_project_identifier_value
        for staged_relationship in staged_relationships
    }
    identifier_rows = session.execute(
        select(ProjectIdentifier.value, ProjectIdentifier.project_id).where(
            ProjectIdentifier.identifier_type == IdentifierType.TCG_PIPEDREAM_ID,
            ProjectIdentifier.value.in_(identifier_values),
        )
    ).all()
    identifier_map = {row.value: row.project_id for row in identifier_rows}

    source_project_ids = {
        project_id
        for staged_relationship in staged_relationships
        if (project_id := identifier_map.get(staged_relationship.project_identifier_value))
    }
    related_project_ids = {
        project_id
        for staged_relationship in staged_relationships
        if (project_id := identifier_map.get(staged_relationship.related_project_identifier_value))
    }
    relationship_types = {
        staged_relationship.relationship_type for staged_relationship in staged_relationships
    }

    existing_keys: set[tuple[object, object, RelationshipType]] = set()
    if source_project_ids and related_project_ids and relationship_types:
        existing_rows = session.execute(
            select(
                ProjectRelationship.project_id,
                ProjectRelationship.related_project_id,
                ProjectRelationship.relationship_type,
            ).where(
                ProjectRelationship.project_id.in_(source_project_ids),
                ProjectRelationship.related_project_id.in_(related_project_ids),
                ProjectRelationship.relationship_type.in_(relationship_types),
            )
        ).all()
        existing_keys = {
            (row.project_id, row.related_project_id, row.relationship_type)
            for row in existing_rows
        }

    pending_keys = set(existing_keys)
    new_relationships: list[ProjectRelationship] = []

    for staged_relationship in staged_relationships:
        source_project_id = identifier_map.get(staged_relationship.project_identifier_value)
        related_project_id = identifier_map.get(
            staged_relationship.related_project_identifier_value
        )
        missing_identifiers: list[str] = []
        if source_project_id is None:
            missing_identifiers.append(staged_relationship.project_identifier_value)
        if related_project_id is None:
            missing_identifiers.append(staged_relationship.related_project_identifier_value)

        if missing_identifiers:
            persist_result.unresolved_relationships.append(
                UnresolvedPipedreamRelationship(
                    project_identifier_value=staged_relationship.project_identifier_value,
                    related_project_identifier_value=staged_relationship.related_project_identifier_value,
                    relationship_type=staged_relationship.relationship_type,
                    source_field=staged_relationship.source_field,
                    missing_identifiers=missing_identifiers,
                )
            )
            logger.warning(
                "Unresolved staged relationship %s -> %s (%s) missing identifiers=%s",
                staged_relationship.project_identifier_value,
                staged_relationship.related_project_identifier_value,
                staged_relationship.relationship_type.value,
                missing_identifiers,
            )
            continue

        if source_project_id == related_project_id:
            persist_result.skipped_existing_relationships += 1
            continue

        relationship_key = (
            source_project_id,
            related_project_id,
            staged_relationship.relationship_type,
        )
        if relationship_key in pending_keys:
            persist_result.skipped_existing_relationships += 1
            continue

        pending_keys.add(relationship_key)
        new_relationships.append(
            ProjectRelationship(
                project_id=source_project_id,
                related_project_id=related_project_id,
                relationship_type=staged_relationship.relationship_type,
                notes=staged_relationship.notes,
            )
        )

    if new_relationships:
        session.add_all(new_relationships)
        session.flush()
        persist_result.created_relationships = len(new_relationships)
