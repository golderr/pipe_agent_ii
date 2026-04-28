from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.schemas import ProjectGeocodeMutationResponse, ProjectGeocodingResponse
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import ChangeLog, ChangeType, GeocodeConfidence, Priority, Project
from tcg_pipeline.geocoding.types import GeocodeAddress, GeocodeResult, ProjectGeocoder
from tcg_pipeline.ingesters._common import build_location
from tcg_pipeline.matching.normalizer import normalize_address


def geocode_project(
    session: Session,
    *,
    project_id: uuid.UUID,
    user: AuthenticatedUser,
    geocoder: ProjectGeocoder | None,
) -> ProjectGeocodeMutationResponse:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    result = _run_geocoder(
        geocoder,
        address=_geocode_address_for_project(project),
    )
    now = datetime.now(UTC)
    actor = user.email or str(user.user_id)
    old_coordinates = _coordinate_payload(project)
    updated_coordinates = False

    if result.is_accepted:
        project.lat = result.latitude
        project.lng = result.longitude
        project.location = build_location(result.latitude, result.longitude)
        project.geocode_confidence = result.confidence
        project.last_editor = actor[:50]
        project.last_edit_date = now.date()
        updated_coordinates = old_coordinates != _coordinate_payload(project)

    _write_geocode_change_log(
        session,
        project=project,
        user=user,
        actor=actor,
        timestamp=now,
        old_coordinates=old_coordinates,
        result=result,
        updated_coordinates=updated_coordinates,
    )
    session.flush()
    return ProjectGeocodeMutationResponse(
        project_id=project.id,
        geocoding=_geocoding_response(result),
        latitude=project.lat,
        longitude=project.lng,
        geocode_confidence=project.geocode_confidence.value,
        updated_coordinates=updated_coordinates,
        change_log_entries_created=1,
    )


def _geocode_address_for_project(project: Project) -> GeocodeAddress:
    normalized_address = normalize_address(
        project.canonical_address,
        city=project.city,
        state=project.state,
        postal_code=project.zip,
        market=project.market,
    )
    return GeocodeAddress(
        address=normalized_address.canonical_street_line or project.canonical_address,
        city=project.city,
        state=project.state,
        zip_code=normalized_address.postal_code or project.zip,
    )


def _run_geocoder(
    geocoder: ProjectGeocoder | None,
    *,
    address: GeocodeAddress,
) -> GeocodeResult:
    if geocoder is None:
        return GeocodeResult(
            status="skipped",
            message="Geocoding service is not configured.",
            fallback_reason="geocoding_not_configured",
        )
    try:
        return geocoder.geocode(address)
    except Exception:
        return GeocodeResult(
            status="failed",
            message="Geocoding request failed.",
        )


def _write_geocode_change_log(
    session: Session,
    *,
    project: Project,
    user: AuthenticatedUser,
    actor: str,
    timestamp: datetime,
    old_coordinates: dict[str, object],
    result: GeocodeResult,
    updated_coordinates: bool,
) -> None:
    session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=timestamp,
            source="manual_geocode",
            field="geocoding",
            old_value=serialize_json(old_coordinates),
            new_value=serialize_json(
                {
                    **_coordinate_payload(project),
                    "updated_coordinates": updated_coordinates,
                    "geocoding": result.as_audit_dict(),
                }
            ),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.LOW,
            reviewed_by=actor[:50],
            reviewed_by_user_id=user.user_id,
            reviewed_by_email=user.email,
        )
    )


def _coordinate_payload(project: Project) -> dict[str, object]:
    confidence = project.geocode_confidence
    return {
        "latitude": project.lat,
        "longitude": project.lng,
        "geocode_confidence": confidence.value
        if isinstance(confidence, GeocodeConfidence)
        else str(confidence),
    }


def _geocoding_response(result: GeocodeResult) -> ProjectGeocodingResponse:
    return ProjectGeocodingResponse(
        status=result.status,
        provider=result.provider,
        confidence=result.confidence.value,
        formatted_address=result.formatted_address,
        accuracy_type=result.accuracy_type,
        accuracy_score=result.accuracy_score,
        fallback_used=result.fallback_used,
        message=result.message,
    )
