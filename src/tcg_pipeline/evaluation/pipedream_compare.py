from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import IdentifierType, Project, ProjectIdentifier
from tcg_pipeline.ingesters.pipedream import PipedreamImportResult, PipedreamProjectRecord

COMPARE_FIELDS = ("pipeline_status", "developer", "total_units", "location")
LOCATION_TOLERANCE_METERS = 100.0


@dataclass(slots=True)
class PipedreamFieldComparison:
    field_name: str
    status: str
    tcg_value: Any
    pipedream_value: Any
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def disagrees(self) -> bool:
        return self.status in {"mismatch", "missing_tcg", "missing_pipedream"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "status": self.status,
            "tcg_value": _jsonable(self.tcg_value),
            "pipedream_value": _jsonable(self.pipedream_value),
            "detail": _jsonable(self.detail),
        }


@dataclass(slots=True)
class PipedreamProjectComparison:
    project_id: uuid.UUID
    pipedream_project_id: str
    project_name: str | None
    canonical_address: str
    zip_code: str | None
    last_evidence_date: date | None
    fields: list[PipedreamFieldComparison]

    @property
    def disagreement_count(self) -> int:
        return sum(1 for field_result in self.fields if field_result.disagrees)

    @property
    def has_disagreement(self) -> bool:
        return self.disagreement_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "pipedream_project_id": self.pipedream_project_id,
            "project_name": self.project_name,
            "canonical_address": self.canonical_address,
            "zip_code": self.zip_code,
            "last_evidence_date": _jsonable(self.last_evidence_date),
            "fields": [field_result.to_dict() for field_result in self.fields],
            "disagreement_count": self.disagreement_count,
        }


@dataclass(slots=True)
class PipedreamCoverageCompareResult:
    market: str
    publication_date: date
    compare_window_start: date
    compare_window_end: date
    zip_codes: list[str]
    compared_projects: list[PipedreamProjectComparison]
    unmatched_pipedream_ids: list[str] = field(default_factory=list)
    excluded_market_count: int = 0
    excluded_zip_count: int = 0
    excluded_evidence_window_count: int = 0

    @property
    def compared_count(self) -> int:
        return len(self.compared_projects)

    @property
    def projects_with_disagreements_count(self) -> int:
        return sum(1 for comparison in self.compared_projects if comparison.has_disagreement)

    @property
    def field_disagreement_count(self) -> int:
        return sum(comparison.disagreement_count for comparison in self.compared_projects)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "publication_date": self.publication_date.isoformat(),
            "compare_window_start": self.compare_window_start.isoformat(),
            "compare_window_end": self.compare_window_end.isoformat(),
            "zip_codes": self.zip_codes,
            "compared_count": self.compared_count,
            "projects_with_disagreements_count": self.projects_with_disagreements_count,
            "field_disagreement_count": self.field_disagreement_count,
            "unmatched_pipedream_ids": self.unmatched_pipedream_ids,
            "excluded_market_count": self.excluded_market_count,
            "excluded_zip_count": self.excluded_zip_count,
            "excluded_evidence_window_count": self.excluded_evidence_window_count,
            "compared_projects": [
                comparison.to_dict() for comparison in self.compared_projects
            ],
        }


def compare_pipedream_coverage(
    session: Session,
    import_results: Sequence[PipedreamImportResult],
    *,
    market: str,
    publication_date: date,
    compare_window_days: int = 28,
    zip_codes: Sequence[str] | None = None,
    location_tolerance_meters: float = LOCATION_TOLERANCE_METERS,
) -> PipedreamCoverageCompareResult:
    compare_window_start = publication_date - timedelta(days=compare_window_days)
    compare_window_end = publication_date + timedelta(days=compare_window_days)
    normalized_zip_codes = sorted({_normalize_zip(zip_code) for zip_code in zip_codes or []})
    records = [
        record
        for import_result in import_results
        for record in import_result.project_records
    ]
    projects_by_pipedream_id = _projects_by_pipedream_id(
        session,
        [record.project_identifier_value for record in records],
    )
    result = PipedreamCoverageCompareResult(
        market=market,
        publication_date=publication_date,
        compare_window_start=compare_window_start,
        compare_window_end=compare_window_end,
        zip_codes=normalized_zip_codes,
        compared_projects=[],
    )

    for record in records:
        project = projects_by_pipedream_id.get(record.project_identifier_value)
        if project is None:
            result.unmatched_pipedream_ids.append(record.project_identifier_value)
            continue
        if project.market != market:
            result.excluded_market_count += 1
            continue
        if normalized_zip_codes and not _project_or_record_in_zips(
            project,
            record,
            normalized_zip_codes,
        ):
            result.excluded_zip_count += 1
            continue
        if not _in_evidence_window(
            project.last_evidence_date,
            start=compare_window_start,
            end=compare_window_end,
        ):
            result.excluded_evidence_window_count += 1
            continue
        result.compared_projects.append(
            _compare_project(
                project,
                record,
                location_tolerance_meters=location_tolerance_meters,
            )
        )

    result.compared_projects.sort(
        key=lambda comparison: (
            not comparison.has_disagreement,
            comparison.zip_code or "",
            comparison.canonical_address,
            comparison.pipedream_project_id,
        )
    )
    result.unmatched_pipedream_ids.sort()
    return result


def _projects_by_pipedream_id(
    session: Session,
    pipedream_ids: list[str],
) -> dict[str, Project]:
    unique_ids = sorted(set(pipedream_ids))
    if not unique_ids:
        return {}
    rows = (
        session.execute(
            select(ProjectIdentifier, Project)
            .join(Project, ProjectIdentifier.project_id == Project.id)
            .where(
                ProjectIdentifier.identifier_type == IdentifierType.TCG_PIPEDREAM_ID,
                ProjectIdentifier.value.in_(unique_ids),
            )
        )
        .all()
    )
    return {identifier.value: project for identifier, project in rows}


def _compare_project(
    project: Project,
    record: PipedreamProjectRecord,
    *,
    location_tolerance_meters: float,
) -> PipedreamProjectComparison:
    pipedream_project = record.project
    fields = [
        _compare_scalar(
            "pipeline_status",
            _enum_value(project.pipeline_status),
            _enum_value(pipedream_project.pipeline_status),
        ),
        _compare_scalar("developer", project.developer, pipedream_project.developer),
        _compare_scalar("total_units", project.total_units, pipedream_project.total_units),
        _compare_location(
            project,
            pipedream_project,
            location_tolerance_meters=location_tolerance_meters,
        ),
    ]
    return PipedreamProjectComparison(
        project_id=project.id,
        pipedream_project_id=record.project_identifier_value,
        project_name=project.project_name,
        canonical_address=project.canonical_address,
        zip_code=project.zip,
        last_evidence_date=project.last_evidence_date,
        fields=fields,
    )


def _compare_scalar(
    field_name: str,
    tcg_value: Any,
    pipedream_value: Any,
) -> PipedreamFieldComparison:
    normalized_tcg = _normalize_compare_value(tcg_value)
    normalized_pipedream = _normalize_compare_value(pipedream_value)
    if normalized_tcg == normalized_pipedream:
        status = "match"
    elif normalized_tcg is None:
        status = "missing_tcg"
    elif normalized_pipedream is None:
        status = "missing_pipedream"
    else:
        status = "mismatch"
    return PipedreamFieldComparison(
        field_name=field_name,
        status=status,
        tcg_value=tcg_value,
        pipedream_value=pipedream_value,
    )


def _compare_location(
    project: Project,
    pipedream_project: Project,
    *,
    location_tolerance_meters: float,
) -> PipedreamFieldComparison:
    tcg_value = _location_value(project)
    pipedream_value = _location_value(pipedream_project)
    detail: dict[str, Any] = {"tolerance_meters": location_tolerance_meters}
    if project.lat is not None and project.lng is not None:
        if pipedream_project.lat is not None and pipedream_project.lng is not None:
            distance_meters = _distance_meters(
                project.lat,
                project.lng,
                pipedream_project.lat,
                pipedream_project.lng,
            )
            detail["distance_meters"] = round(distance_meters, 2)
            status = "match" if distance_meters <= location_tolerance_meters else "mismatch"
            return PipedreamFieldComparison(
                field_name="location",
                status=status,
                tcg_value=tcg_value,
                pipedream_value=pipedream_value,
                detail=detail,
            )
        return PipedreamFieldComparison(
            field_name="location",
            status="missing_pipedream",
            tcg_value=tcg_value,
            pipedream_value=pipedream_value,
            detail=detail,
        )
    if pipedream_project.lat is not None and pipedream_project.lng is not None:
        return PipedreamFieldComparison(
            field_name="location",
            status="missing_tcg",
            tcg_value=tcg_value,
            pipedream_value=pipedream_value,
            detail=detail,
        )
    return _compare_scalar(
        "location",
        project.canonical_address,
        pipedream_project.canonical_address,
    )


def _project_or_record_in_zips(
    project: Project,
    record: PipedreamProjectRecord,
    zip_codes: list[str],
) -> bool:
    project_zip = _normalize_zip(project.zip)
    record_zip = _normalize_zip(record.project.zip)
    return project_zip in zip_codes or record_zip in zip_codes


def _in_evidence_window(value: date | None, *, start: date, end: date) -> bool:
    return value is not None and start <= value <= end


def _normalize_compare_value(value: Any) -> Any:
    value = _enum_value(value)
    if value is None:
        return None
    if isinstance(value, str):
        normalized = " ".join(value.split()).casefold()
        return normalized or None
    return value


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _location_value(project: Project) -> dict[str, Any]:
    return {
        "lat": project.lat,
        "lng": project.lng,
        "canonical_address": project.canonical_address,
    }


def _distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_meters = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_meters * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _normalize_zip(value: Any) -> str:
    text = str(value or "").strip()
    return text[:5]


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(nested_value) for key, nested_value in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return _enum_value(value)
