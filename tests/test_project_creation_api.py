from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session
from tcg_pipeline.api.main import create_app
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    GeocodeConfidence,
    Jurisdiction,
    Market,
    PipelineStatus,
    Project,
    StatusHistory,
)
from tcg_pipeline.geocoding.types import GeocodeAddress, GeocodeResult
from tcg_pipeline.settings import Settings

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeVerifier:
    def verify(self, token: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=USER_ID,
            email="allowed@example.com",
            role="authenticated",
            claims={
                "sub": str(USER_ID),
                "email": "allowed@example.com",
                "role": "authenticated",
            },
        )


def test_create_project_creates_project_and_audit_rows(postgres_session: Session) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("create")
    postgres_session.add_all([market, jurisdiction])
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "123 w first st",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
            "project_name": "Manual Project",
            "zip": "90012",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    project = postgres_session.get(Project, uuid.UUID(body["project_id"]))
    assert project is not None
    status_history = postgres_session.execute(
        select(StatusHistory).where(StatusHistory.project_id == project.id)
    ).scalar_one()
    change_log = postgres_session.execute(
        select(ChangeLog).where(ChangeLog.project_id == project.id)
    ).scalar_one()

    assert body["created"] is True
    assert body["duplicate_candidates"] == []
    assert body["canonical_address"] == "123 WEST 1ST STREET LOS ANGELES CA 90012"
    assert project.canonical_address == "123 WEST 1ST STREET LOS ANGELES CA 90012"
    assert project.project_name == "Manual Project"
    assert project.market == market.slug
    assert project.market_id == market.id
    assert project.jurisdiction_id == jurisdiction.id
    assert project.city == "Los Angeles"
    assert project.county == "Los Angeles"
    assert project.zip == "90012"
    assert project.created_by == "allowed@example.com"
    assert status_history.status == PipelineStatus.PROPOSED
    assert status_history.source == "manual_project"
    assert change_log.source == "manual_project"
    assert change_log.change_type == ChangeType.RESEARCHER_CONFIRMED
    assert change_log.new_value["canonical_address"] == project.canonical_address
    assert change_log.reviewed_by_user_id == USER_ID
    assert change_log.reviewed_by_email == "allowed@example.com"


def test_create_project_geocodes_manual_project(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("geocode")
    postgres_session.add_all([market, jurisdiction])
    postgres_session.flush()
    fake_geocoder = _FakeProjectGeocoder(
        GeocodeResult(
            status="accepted",
            provider="geocodio",
            latitude=34.0522,
            longitude=-118.2437,
            formatted_address="123 W 1st St, Los Angeles, CA 90012",
            accuracy_type="rooftop",
            accuracy_score=1.0,
            confidence=GeocodeConfidence.HIGH,
        )
    )
    _patch_project_geocoder(monkeypatch, fake_geocoder)
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "123 w first st",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
            "zip": "90012",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    project = postgres_session.get(Project, uuid.UUID(body["project_id"]))
    change_log = postgres_session.execute(
        select(ChangeLog).where(ChangeLog.project_id == project.id)
    ).scalar_one()

    assert project is not None
    assert project.lat == 34.0522
    assert project.lng == -118.2437
    assert project.location is not None
    assert project.geocode_confidence == GeocodeConfidence.HIGH
    assert body["geocoding"]["status"] == "accepted"
    assert body["geocoding"]["provider"] == "geocodio"
    assert change_log.new_value["geocoding"]["provider"] == "geocodio"
    assert change_log.new_value["geocoding"]["confidence"] == "high"
    assert fake_geocoder.calls[0] == GeocodeAddress(
        address="123 WEST 1ST STREET",
        city="Los Angeles",
        state="CA",
        zip_code="90012",
    )


def test_create_project_geocoding_failure_does_not_block_create(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("geocode-fail")
    postgres_session.add_all([market, jurisdiction])
    postgres_session.flush()
    _patch_project_geocoder(
        monkeypatch,
        _FakeProjectGeocoder(
            GeocodeResult(
                status="low_confidence",
                message="No reliable Geocodio or Esri geocode result.",
            )
        ),
    )
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "222 uncertain way",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
            "zip": "90012",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    project = postgres_session.get(Project, uuid.UUID(body["project_id"]))

    assert body["created"] is True
    assert body["geocoding"]["status"] == "low_confidence"
    assert project is not None
    assert project.lat is None
    assert project.lng is None
    assert project.location is None
    assert project.geocode_confidence == GeocodeConfidence.NONE


def test_geocode_project_updates_missing_coordinates(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("regeocode")
    project = _project(
        "555 WEST MAP STREET LOS ANGELES CA 90012",
        market=market,
        jurisdiction=jurisdiction,
    )
    postgres_session.add_all([market, jurisdiction, project])
    postgres_session.flush()
    fake_geocoder = _FakeProjectGeocoder(
        GeocodeResult(
            status="accepted",
            provider="esri",
            latitude=34.055,
            longitude=-118.25,
            formatted_address="555 W Map St, Los Angeles, CA 90012",
            accuracy_type="PointAddress",
            accuracy_score=96.0,
            confidence=GeocodeConfidence.HIGH,
            fallback_used=True,
            fallback_reason="geocodio_not_high_confidence",
        )
    )
    _patch_project_geocoder(monkeypatch, fake_geocoder)
    client = _client(postgres_session)

    response = client.post(f"/projects/{project.id}/geocode", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    postgres_session.refresh(project)
    change_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.source == "manual_geocode",
        )
    ).scalar_one()

    assert body["geocoding"]["status"] == "accepted"
    assert body["geocoding"]["provider"] == "esri"
    assert body["latitude"] == 34.055
    assert body["longitude"] == -118.25
    assert body["geocode_confidence"] == "high"
    assert body["updated_coordinates"] is True
    assert project.lat == 34.055
    assert project.lng == -118.25
    assert project.location is not None
    assert project.geocode_confidence == GeocodeConfidence.HIGH
    assert project.last_editor == "allowed@example.com"
    assert change_log.old_value["latitude"] is None
    assert change_log.new_value["latitude"] == 34.055
    assert change_log.new_value["updated_coordinates"] is True
    assert change_log.new_value["geocoding"]["fallback_used"] is True
    assert change_log.reviewed_by_user_id == USER_ID
    assert change_log.reviewed_by_email == "allowed@example.com"
    assert fake_geocoder.calls[0] == GeocodeAddress(
        address="555 WEST MAP STREET",
        city="Los Angeles",
        state="CA",
        zip_code="90012",
    )


def test_geocode_project_audits_skipped_when_keys_missing(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("regeocode-skip")
    project = _project(
        "556 WEST SKIP STREET LOS ANGELES CA 90012",
        market=market,
        jurisdiction=jurisdiction,
    )
    postgres_session.add_all([market, jurisdiction, project])
    postgres_session.flush()
    _patch_project_geocoder(
        monkeypatch,
        _FakeProjectGeocoder(
            GeocodeResult(
                status="skipped",
                message="Geocoding service is not configured.",
                fallback_reason="geocoding_not_configured",
            )
        ),
    )
    client = _client(postgres_session)

    response = client.post(f"/projects/{project.id}/geocode", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    postgres_session.refresh(project)
    change_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.source == "manual_geocode",
        )
    ).scalar_one()

    assert body["geocoding"]["status"] == "skipped"
    assert body["updated_coordinates"] is False
    assert body["latitude"] is None
    assert body["longitude"] is None
    assert project.lat is None
    assert project.lng is None
    assert project.geocode_confidence == GeocodeConfidence.NONE
    assert change_log.new_value["updated_coordinates"] is False
    assert change_log.new_value["geocoding"]["status"] == "skipped"


def test_geocode_project_keeps_existing_coordinates_on_low_confidence(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("regeocode-low")
    project = _project(
        "557 WEST LOW STREET LOS ANGELES CA 90012",
        market=market,
        jurisdiction=jurisdiction,
        lat=34.0,
        lng=-118.0,
        geocode_confidence=GeocodeConfidence.MEDIUM,
    )
    postgres_session.add_all([market, jurisdiction, project])
    postgres_session.flush()
    _patch_project_geocoder(
        monkeypatch,
        _FakeProjectGeocoder(
            GeocodeResult(
                status="low_confidence",
                message="No reliable Geocodio or Esri geocode result.",
            )
        ),
    )
    client = _client(postgres_session)

    response = client.post(f"/projects/{project.id}/geocode", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    postgres_session.refresh(project)
    change_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.source == "manual_geocode",
        )
    ).scalar_one()

    assert body["geocoding"]["status"] == "low_confidence"
    assert body["updated_coordinates"] is False
    assert body["latitude"] == 34.0
    assert body["longitude"] == -118.0
    assert project.lat == 34.0
    assert project.lng == -118.0
    assert project.geocode_confidence == GeocodeConfidence.MEDIUM
    assert change_log.old_value["latitude"] == 34.0
    assert change_log.new_value["latitude"] == 34.0
    assert change_log.new_value["geocoding"]["status"] == "low_confidence"


def test_geocode_project_returns_404_for_missing_project(postgres_session: Session) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    client = _client(postgres_session)

    response = client.post(f"/projects/{uuid.uuid4()}/geocode", headers=_auth_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found."


def test_create_project_returns_duplicate_candidate_without_force(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("dupe")
    existing = _project(
        "456 SOUTH MAIN STREET LOS ANGELES CA 90012",
        market=market,
        jurisdiction=jurisdiction,
        project_name="Existing",
    )
    postgres_session.add_all([market, jurisdiction, existing])
    postgres_session.flush()
    fake_geocoder = _FakeProjectGeocoder(
        GeocodeResult(status="accepted", provider="geocodio", latitude=1, longitude=2)
    )
    _patch_project_geocoder(monkeypatch, fake_geocoder)
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "456 S Main St",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
            "zip": "90012",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    project_count = len(
        postgres_session.execute(
            select(Project).where(Project.market_id == market.id)
        ).scalars().all()
    )
    assert body["created"] is False
    assert body["project_id"] is None
    assert body["duplicate_candidates"][0]["project_id"] == str(existing.id)
    assert body["duplicate_candidates"][0]["match_type"] == "address"
    assert body["change_log_entries_created"] == 0
    assert body["geocoding"] is None
    assert project_count == 1
    assert fake_geocoder.calls == []


def test_create_project_force_creates_despite_duplicate(postgres_session: Session) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("force")
    existing = _project(
        "789 WEST TEMPLE STREET LOS ANGELES CA 90012",
        market=market,
        jurisdiction=jurisdiction,
    )
    postgres_session.add_all([market, jurisdiction, existing])
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "789 W Temple St",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
            "zip": "90012",
            "force_create": True,
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    project_count = len(
        postgres_session.execute(
            select(Project).where(Project.market_id == market.id)
        ).scalars().all()
    )
    assert body["created"] is True
    assert body["project_id"] is not None
    assert body["duplicate_candidates"][0]["project_id"] == str(existing.id)
    assert project_count == 2


def test_create_project_force_returns_duplicate_when_unique_lock_loses(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("race")
    existing = _project(
        "321 WEST RACE STREET LOS ANGELES CA 90012",
        market=market,
        jurisdiction=jurisdiction,
        project_name="Race Winner",
    )
    postgres_session.add_all([market, jurisdiction, existing])
    postgres_session.flush()
    original_flush = postgres_session.flush

    def fake_flush(*args: object, **kwargs: object) -> None:
        pending_duplicate = any(
            isinstance(obj, Project)
            and obj is not existing
            and obj.canonical_address == existing.canonical_address
            and obj.market_id == market.id
            for obj in postgres_session.new
        )
        if pending_duplicate:
            raise IntegrityError(
                "INSERT",
                {},
                Exception("uq_projects_market_id_canonical_address"),
            )
        original_flush(*args, **kwargs)

    monkeypatch.setattr(postgres_session, "flush", fake_flush)
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "321 W Race St",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
            "zip": "90012",
            "force_create": True,
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    project_count = len(
        postgres_session.execute(
            select(Project).where(Project.market_id == market.id)
        ).scalars().all()
    )

    assert body["created"] is False
    assert body["project_id"] is None
    assert body["canonical_address"] == existing.canonical_address
    assert body["duplicate_candidates"][0]["project_id"] == str(existing.id)
    assert body["duplicate_candidates"][0]["match_type"] == "address"
    assert body["change_log_entries_created"] == 0
    assert body["geocoding"] is None
    assert project_count == 1


def test_create_project_rejects_jurisdiction_from_other_market(
    postgres_session: Session,
) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, _jurisdiction = _market_and_jurisdiction("market-a")
    other_market, other_jurisdiction = _market_and_jurisdiction("market-b")
    postgres_session.add_all([market, other_market, other_jurisdiction])
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "100 mismatch way",
            "market_id": str(market.id),
            "jurisdiction_id": str(other_jurisdiction.id),
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "jurisdiction_id must belong to market_id."


def test_create_project_rejects_blank_address(postgres_session: Session) -> None:
    _ensure_project_creation_api_tables(postgres_session)
    market, jurisdiction = _market_and_jurisdiction("blank")
    postgres_session.add_all([market, jurisdiction])
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        "/projects",
        json={
            "canonical_address": "   ",
            "market_id": str(market.id),
            "jurisdiction_id": str(jurisdiction.id),
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "canonical_address is required."


def _client(postgres_session: Session) -> TestClient:
    app = create_app(
        settings=Settings(
            app_env="test",
            database_url=None,
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",
            allowed_emails="allowed@example.com",
        ),
        jwt_verifier=FakeVerifier(),
        readiness_check=lambda: None,
    )

    def override_db_session() -> Iterator[Session]:
        yield postgres_session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer valid-token"}


class _FakeProjectGeocoder:
    def __init__(self, result: GeocodeResult) -> None:
        self.result = result
        self.calls: list[GeocodeAddress] = []

    def geocode(self, address: GeocodeAddress) -> GeocodeResult:
        self.calls.append(address)
        return self.result


def _patch_project_geocoder(
    monkeypatch: pytest.MonkeyPatch,
    geocoder: _FakeProjectGeocoder,
) -> None:
    from tcg_pipeline.api.routers import projects

    monkeypatch.setattr(projects, "geocoder_from_settings", lambda _settings: geocoder)


def _ensure_project_creation_api_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "markets",
        "jurisdictions",
        "projects",
        "status_history",
        "change_log",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(
            f"Apply the latest migrations before running project creation tests: {missing}"
        )


def _market_and_jurisdiction(suffix: str) -> tuple[Market, Jurisdiction]:
    market = Market(
        id=uuid.uuid4(),
        slug=f"test_market_{suffix}_{uuid.uuid4().hex[:8]}",
        name="Los Angeles County",
        display_name="Los Angeles County",
        state="CA",
        market_type="county",
    )
    jurisdiction = Jurisdiction(
        id=uuid.uuid4(),
        slug=f"test_jurisdiction_{suffix}_{uuid.uuid4().hex[:8]}",
        name="City of Los Angeles",
        display_name="Los Angeles",
        state="CA",
        market=market,
        entity_type="city",
    )
    return market, jurisdiction


def _project(
    canonical_address: str,
    *,
    market: Market,
    jurisdiction: Jurisdiction,
    **overrides: Any,
) -> Project:
    defaults: dict[str, Any] = {
        "raw_addresses": [canonical_address],
        "market": market.slug,
        "market_id": market.id,
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
        "jurisdiction": jurisdiction.name,
        "jurisdiction_id": jurisdiction.id,
        "pipeline_status": PipelineStatus.PROPOSED,
    }
    defaults.update(overrides)
    return Project(canonical_address=canonical_address, **defaults)
