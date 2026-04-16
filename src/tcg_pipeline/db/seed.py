from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    IdentifierType,
    ProjectIdentifier,
    ProjectRelationship,
    RelationshipType,
)
from tcg_pipeline.ingesters.pipedream import (
    PIPEDREAM_SOURCE_NAME,
    PipedreamImportResult,
    PipedreamIngester,
    StagedProjectRelationship,
)

logger = logging.getLogger(__name__)


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
    unresolved_relationships: list[UnresolvedPipedreamRelationship] = field(default_factory=list)

    @property
    def unresolved_relationship_count(self) -> int:
        return len(self.unresolved_relationships)


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

    session.add_all([project_record.project for project_record in project_records])
    session.add_all(dismissed_records)
    session.flush()

    persist_result = PipedreamPersistResult(
        inserted_projects=len(project_records),
        inserted_dismissed_records=len(dismissed_records),
    )
    _persist_staged_relationships(session, staged_relationships, persist_result)
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
        related_project_id = identifier_map.get(staged_relationship.related_project_identifier_value)
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
