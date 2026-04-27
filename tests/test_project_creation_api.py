from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session
from tcg_pipeline.api.main import create_app
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    Jurisdiction,
    Market,
    PipelineStatus,
    Project,
    StatusHistory,
)
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


def test_create_project_returns_duplicate_candidate_without_force(
    postgres_session: Session,
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
    assert project_count == 1


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
