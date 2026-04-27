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
    PipelineStatus,
    Project,
    ProjectRelationship,
    RelationshipType,
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


def test_add_project_relationship_creates_row_and_logs(postgres_session: Session) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("930 RELATIONSHIP WAY LOS ANGELES CA 90012", project_name="Source")
    related = _project("931 RELATED WAY LOS ANGELES CA 90012", project_name="Target")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/relationship",
        json={
            "relationship_type": "phase",
            "related_project_id": str(related.id),
            "notes": " Phase sibling ",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    postgres_session.refresh(project)
    relationship = postgres_session.execute(
        select(ProjectRelationship).where(ProjectRelationship.project_id == project.id)
    ).scalar_one()
    change_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "relationships",
        )
    ).scalar_one()

    assert body["created"] is True
    assert body["relationship_type"] == "phase"
    assert body["related_project_id"] == str(related.id)
    assert body["notes"] == "Phase sibling"
    assert project.last_editor == "allowed@example.com"
    assert relationship.related_project_id == related.id
    assert relationship.relationship_type == RelationshipType.PHASE
    assert relationship.notes == "Phase sibling"
    assert change_log.source == "project_relationship"
    assert change_log.change_type == ChangeType.RESEARCHER_CONFIRMED
    assert change_log.new_value["related_project_id"] == str(related.id)


def test_add_project_relationship_is_idempotent(postgres_session: Session) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("932 DUP RELATIONSHIP WAY LOS ANGELES CA 90012")
    related = _project("933 DUP RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    payload = {"relationship_type": "master_plan", "related_project_id": str(related.id)}

    first = client.post(
        f"/projects/{project.id}/relationship",
        json=payload,
        headers=_auth_headers(),
    )
    second = client.post(
        f"/projects/{project.id}/relationship",
        json=payload,
        headers=_auth_headers(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["change_log_entries_created"] == 0
    relationship_count = len(
        postgres_session.execute(
            select(ProjectRelationship).where(ProjectRelationship.project_id == project.id)
        ).scalars().all()
    )
    change_log_count = len(
        postgres_session.execute(
            select(ChangeLog).where(ChangeLog.project_id == project.id)
        ).scalars().all()
    )
    assert relationship_count == 1
    assert change_log_count == 1


@pytest.mark.parametrize(
    ("relationship_type", "expected_status", "message"),
    [
        ("not_a_type", 422, "relationship_type must be one of:"),
        ("phase", 422, "Cannot relate a project to itself."),
    ],
)
def test_add_project_relationship_rejects_invalid_requests(
    postgres_session: Session,
    relationship_type: str,
    expected_status: int,
    message: str,
) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("934 INVALID RELATIONSHIP WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/relationship",
        json={"relationship_type": relationship_type, "related_project_id": str(project.id)},
        headers=_auth_headers(),
    )

    assert response.status_code == expected_status
    assert message in response.json()["detail"]


def test_add_project_relationship_returns_404_for_missing_related_project(
    postgres_session: Session,
) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("935 MISSING RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/relationship",
        json={"relationship_type": "phase", "related_project_id": str(uuid.uuid4())},
        headers=_auth_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found."


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


def _ensure_relationship_api_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {"project_relationships", "change_log"}
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(
            f"Apply the latest migrations before running relationship API tests: {missing}"
        )


def _project(canonical_address: str, **overrides: Any) -> Project:
    defaults: dict[str, Any] = {
        "raw_addresses": [canonical_address],
        "market": "los_angeles",
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
        "pipeline_status": PipelineStatus.PROPOSED,
    }
    defaults.update(overrides)
    return Project(canonical_address=canonical_address, **defaults)
