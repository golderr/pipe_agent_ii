from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.schemas import (
    ProjectCreateCandidate,
    ProjectCreateResponse,
)
from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    Jurisdiction,
    Market,
    PipelineStatus,
    Priority,
    Project,
    StatusHistory,
)
from tcg_pipeline.matching.matcher import match_raw_record
from tcg_pipeline.matching.normalizer import normalize_address


def create_project(
    session: Session,
    *,
    canonical_address: str,
    market_id: uuid.UUID,
    jurisdiction_id: uuid.UUID,
    project_name: str | None,
    city: str | None,
    county: str | None,
    zip_code: str | None,
    force_create: bool,
    user: AuthenticatedUser,
) -> ProjectCreateResponse:
    market = _load_market(session, market_id)
    jurisdiction = _load_jurisdiction(session, jurisdiction_id)
    if jurisdiction.market_id != market.id:
        raise HTTPException(
            status_code=422,
            detail="jurisdiction_id must belong to market_id.",
        )

    normalized_city = _clean_text(city) or _default_city_for_jurisdiction(jurisdiction)
    normalized_county = _clean_text(county) or _default_county_for_market(market)
    normalized_zip = _clean_text(zip_code)
    raw_address = _require_text(canonical_address, "canonical_address")
    normalized_address = normalize_address(
        raw_address,
        city=normalized_city,
        state=jurisdiction.state,
        postal_code=normalized_zip,
        market=market.slug,
    )
    if not normalized_address.canonical_address:
        raise HTTPException(
            status_code=422,
            detail="canonical_address could not be normalized.",
        )

    match_result = match_raw_record(
        session,
        market=market.slug,
        raw_record=RawRecord(
            source_name="manual_project",
            source_record_id=f"manual:{uuid.uuid4()}",
            raw_payload={"canonical_address": raw_address},
            canonical_address=normalized_address.canonical_address,
        ),
    )
    duplicate_ids = (
        [match_result.project_id]
        if match_result.project_id is not None
        else list(match_result.candidate_project_ids)
    )
    duplicate_candidates = _load_duplicate_candidates(
        session,
        project_ids=[project_id for project_id in duplicate_ids if project_id is not None],
        match_type=match_result.match_type,
        confidence=match_result.confidence,
    )
    if duplicate_candidates and not force_create:
        return ProjectCreateResponse(
            created=False,
            project_id=None,
            canonical_address=normalized_address.canonical_address,
            duplicate_candidates=duplicate_candidates,
            change_log_entries_created=0,
        )

    now = datetime.now(UTC)
    actor = _actor_for_audit(user)
    project = Project(
        canonical_address=normalized_address.canonical_address,
        raw_addresses=_unique_texts([raw_address, normalized_address.canonical_address]),
        market=market.slug,
        market_id=market.id,
        city=normalized_city,
        state=jurisdiction.state,
        county=normalized_county,
        zip=normalized_address.postal_code,
        jurisdiction=jurisdiction.name,
        jurisdiction_id=jurisdiction.id,
        project_name=_clean_text(project_name),
        pipeline_status=PipelineStatus.PROPOSED,
        created_by=actor,
        last_editor=actor[:50],
        last_edit_date=now.date(),
    )
    session.add(project)
    session.flush()
    session.add(
        StatusHistory(
            project_id=project.id,
            status=PipelineStatus.PROPOSED,
            status_date=now.date(),
            source="manual_project",
            notes="Created manually from Pipeline.",
        )
    )
    _write_project_created_change_log(
        session,
        project=project,
        actor=actor,
        user=user,
        timestamp=now,
        duplicate_candidates=duplicate_candidates,
    )
    session.flush()
    return ProjectCreateResponse(
        created=True,
        project_id=project.id,
        canonical_address=project.canonical_address,
        duplicate_candidates=duplicate_candidates,
        change_log_entries_created=1,
    )


def _load_market(session: Session, market_id: uuid.UUID) -> Market:
    market = session.get(Market, market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found.")
    return market


def _load_jurisdiction(session: Session, jurisdiction_id: uuid.UUID) -> Jurisdiction:
    jurisdiction = session.get(Jurisdiction, jurisdiction_id)
    if jurisdiction is None:
        raise HTTPException(status_code=404, detail="Jurisdiction not found.")
    return jurisdiction


def _load_duplicate_candidates(
    session: Session,
    *,
    project_ids: list[uuid.UUID],
    match_type: str,
    confidence: float | None,
) -> list[ProjectCreateCandidate]:
    if not project_ids:
        return []

    projects = (
        session.execute(select(Project).where(Project.id.in_(project_ids)))
        .scalars()
        .all()
    )
    project_by_id = {project.id: project for project in projects}
    candidates: list[ProjectCreateCandidate] = []
    for project_id in project_ids:
        project = project_by_id.get(project_id)
        if project is None:
            continue
        candidates.append(
            ProjectCreateCandidate(
                project_id=project.id,
                project_name=project.project_name or project.canonical_address,
                canonical_address=project.canonical_address,
                pipeline_status=project.pipeline_status.value,
                match_type=match_type,
                confidence=confidence,
            )
        )
    return candidates


def _write_project_created_change_log(
    session: Session,
    *,
    project: Project,
    actor: str,
    user: AuthenticatedUser,
    timestamp: datetime,
    duplicate_candidates: list[ProjectCreateCandidate],
) -> None:
    session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=timestamp,
            source="manual_project",
            field="project",
            old_value=None,
            new_value=serialize_json(
                {
                    "project_id": str(project.id),
                    "canonical_address": project.canonical_address,
                    "project_name": project.project_name,
                    "market": project.market,
                    "jurisdiction_id": str(project.jurisdiction_id)
                    if project.jurisdiction_id
                    else None,
                    "duplicate_candidate_ids": [
                        str(candidate.project_id) for candidate in duplicate_candidates
                    ],
                }
            ),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.LOW,
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )


def _default_city_for_jurisdiction(jurisdiction: Jurisdiction) -> str:
    label = jurisdiction.display_name or jurisdiction.name
    for prefix in ("City of ", "Town of ", "County of "):
        if label.startswith(prefix):
            return label.removeprefix(prefix)
    return label


def _default_county_for_market(market: Market) -> str:
    label = market.display_name or market.name
    return label.removesuffix(" County")


def _actor_for_audit(user: AuthenticatedUser) -> str:
    return user.email or str(user.user_id)


def _require_text(value: str | None, field_name: str) -> str:
    text = _clean_text(value)
    if text is None:
        raise HTTPException(status_code=422, detail=f"{field_name} is required.")
    return text


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _unique_texts(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
    return results
