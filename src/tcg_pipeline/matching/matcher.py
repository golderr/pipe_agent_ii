from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.models import IdentifierType, Project, ProjectIdentifier, ProjectSourceRecord

SOURCE_RECORD_MATCH = "source_record"
IDENTIFIER_MATCH = "identifier"
ADDRESS_MATCH = "address"
POSSIBLE_MATCH = "possible_match"
NO_MATCH = "no_match"


@dataclass(slots=True)
class MatchResult:
    project_id: uuid.UUID | None
    match_type: str
    confidence: float | None = None
    candidate_project_ids: list[uuid.UUID] = field(default_factory=list)
    matched_identifier_type: IdentifierType | None = None
    matched_identifier_value: str | None = None


def match_raw_record(
    session: Session,
    *,
    market: str,
    raw_record: RawRecord,
) -> MatchResult:
    source_record_match = session.execute(
        select(ProjectSourceRecord.project_id).where(
            ProjectSourceRecord.source_name == raw_record.source_name,
            ProjectSourceRecord.source_record_id == raw_record.source_record_id,
        )
    ).scalars().first()
    if source_record_match is not None:
        return MatchResult(
            project_id=source_record_match,
            match_type=SOURCE_RECORD_MATCH,
            confidence=0.99,
        )

    identifier_match = _match_identifiers(session, raw_record=raw_record)
    if identifier_match is not None:
        return identifier_match

    address_match = _match_address(session, market=market, raw_record=raw_record)
    if address_match is not None:
        return address_match

    return MatchResult(project_id=None, match_type=NO_MATCH, confidence=0.0)


def _match_identifiers(session: Session, *, raw_record: RawRecord) -> MatchResult | None:
    matched_project_ids: set[uuid.UUID] = set()
    matched_identifier_type: IdentifierType | None = None
    matched_identifier_value: str | None = None

    for identifier_type_name, values in raw_record.identifiers.items():
        identifier_type = _coerce_identifier_type(identifier_type_name)
        if identifier_type is None:
            continue
        cleaned_values = sorted({value for value in values if value})
        if not cleaned_values:
            continue

        identifier_rows = session.execute(
            select(ProjectIdentifier.project_id, ProjectIdentifier.value).where(
                ProjectIdentifier.identifier_type == identifier_type,
                ProjectIdentifier.value.in_(cleaned_values),
            )
        ).all()
        if identifier_rows:
            matched_identifier_type = identifier_type
            matched_identifier_value = cast(str | None, identifier_rows[0].value)
            matched_project_ids.update(row.project_id for row in identifier_rows)

    if len(matched_project_ids) == 1:
        return MatchResult(
            project_id=next(iter(matched_project_ids)),
            match_type=IDENTIFIER_MATCH,
            confidence=0.97,
            matched_identifier_type=matched_identifier_type,
            matched_identifier_value=matched_identifier_value,
        )
    if len(matched_project_ids) > 1:
        return MatchResult(
            project_id=None,
            match_type=POSSIBLE_MATCH,
            confidence=0.7,
            candidate_project_ids=sorted(matched_project_ids),
            matched_identifier_type=matched_identifier_type,
            matched_identifier_value=matched_identifier_value,
        )
    return None


def _match_address(session: Session, *, market: str, raw_record: RawRecord) -> MatchResult | None:
    if not raw_record.canonical_address:
        return None

    matched_project_ids = session.execute(
        select(Project.id).where(
            Project.market == market,
            Project.canonical_address == raw_record.canonical_address,
        )
    ).scalars().all()

    unique_matches = sorted(set(matched_project_ids))
    if len(unique_matches) == 1:
        return MatchResult(
            project_id=unique_matches[0],
            match_type=ADDRESS_MATCH,
            confidence=0.9,
        )
    if len(unique_matches) > 1:
        return MatchResult(
            project_id=None,
            match_type=POSSIBLE_MATCH,
            confidence=0.65,
            candidate_project_ids=unique_matches,
        )
    return None


def _coerce_identifier_type(identifier_type_name: str) -> IdentifierType | None:
    try:
        return IdentifierType(identifier_type_name)
    except ValueError:
        return None
