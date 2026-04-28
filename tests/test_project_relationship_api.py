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
    assert body["updated"] is False
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
    assert change_log.reviewed_by_user_id == USER_ID
    assert change_log.reviewed_by_email == "allowed@example.com"


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
    assert second.json()["updated"] is False
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


def test_add_project_relationship_updates_existing_note(postgres_session: Session) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("936 NOTE RELATIONSHIP WAY LOS ANGELES CA 90012")
    related = _project("937 NOTE RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    payload = {
        "relationship_type": "phase",
        "related_project_id": str(related.id),
        "notes": "Original note",
    }

    first = client.post(
        f"/projects/{project.id}/relationship",
        json=payload,
        headers=_auth_headers(),
    )
    second = client.post(
        f"/projects/{project.id}/relationship",
        json={**payload, "notes": "Updated note"},
        headers=_auth_headers(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    body = second.json()
    relationship = postgres_session.execute(
        select(ProjectRelationship).where(ProjectRelationship.project_id == project.id)
    ).scalar_one()
    change_logs = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "relationships",
        )
    ).scalars().all()

    assert body["created"] is False
    assert body["updated"] is True
    assert body["notes"] == "Updated note"
    assert body["change_log_entries_created"] == 1
    assert relationship.notes == "Updated note"
    assert len(change_logs) == 2
    assert any(
        row.old_value
        and row.old_value["notes"] == "Original note"
        and row.new_value["notes"] == "Updated note"
        for row in change_logs
    )


def test_update_project_relationship_clears_note(postgres_session: Session) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("938 CLEAR RELATIONSHIP WAY LOS ANGELES CA 90012")
    related = _project("939 CLEAR RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    created = client.post(
        f"/projects/{project.id}/relationship",
        json={
            "relationship_type": "phase",
            "related_project_id": str(related.id),
            "notes": "Clear me",
        },
        headers=_auth_headers(),
    )
    relationship_id = created.json()["relationship_id"]

    response = client.patch(
        f"/projects/{project.id}/relationship/{relationship_id}",
        json={"notes": ""},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    relationship = postgres_session.get(ProjectRelationship, uuid.UUID(relationship_id))
    change_logs = postgres_session.execute(
        select(ChangeLog)
        .where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "relationships",
        )
        .order_by(ChangeLog.timestamp)
    ).scalars().all()

    assert body["updated"] is True
    assert body["notes"] is None
    assert relationship is not None
    assert relationship.notes is None
    assert len(change_logs) == 2
    assert change_logs[-1].old_value["notes"] == "Clear me"
    assert change_logs[-1].new_value["notes"] is None


def test_update_project_relationship_retypes_row(postgres_session: Session) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("940 RETYPE RELATIONSHIP WAY LOS ANGELES CA 90012")
    related = _project("941 RETYPE RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    created = client.post(
        f"/projects/{project.id}/relationship",
        json={
            "relationship_type": "phase",
            "related_project_id": str(related.id),
            "notes": "Same note",
        },
        headers=_auth_headers(),
    )
    relationship_id = created.json()["relationship_id"]

    response = client.patch(
        f"/projects/{project.id}/relationship/{relationship_id}",
        json={"relationship_type": "counterpart"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    relationship = postgres_session.get(ProjectRelationship, uuid.UUID(relationship_id))
    change_logs = postgres_session.execute(
        select(ChangeLog)
        .where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "relationships",
        )
        .order_by(ChangeLog.timestamp)
    ).scalars().all()

    assert body["relationship_type"] == "counterpart"
    assert body["notes"] == "Same note"
    assert relationship is not None
    assert relationship.relationship_type == RelationshipType.COUNTERPART
    assert change_logs[-1].old_value["relationship_type"] == "phase"
    assert change_logs[-1].new_value["relationship_type"] == "counterpart"


def test_update_project_relationship_rejects_duplicate_type(
    postgres_session: Session,
) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("942 DUP RETYPE WAY LOS ANGELES CA 90012")
    related = _project("943 DUP RETYPE RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    client.post(
        f"/projects/{project.id}/relationship",
        json={"relationship_type": "phase", "related_project_id": str(related.id)},
        headers=_auth_headers(),
    )
    counterpart = client.post(
        f"/projects/{project.id}/relationship",
        json={"relationship_type": "counterpart", "related_project_id": str(related.id)},
        headers=_auth_headers(),
    )

    response = client.patch(
        f"/projects/{project.id}/relationship/{counterpart.json()['relationship_id']}",
        json={"relationship_type": "phase"},
        headers=_auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Relationship already exists with that type."


def test_delete_project_relationship_removes_row_and_logs(postgres_session: Session) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("944 DELETE RELATIONSHIP WAY LOS ANGELES CA 90012")
    related = _project("945 DELETE RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    created = client.post(
        f"/projects/{project.id}/relationship",
        json={
            "relationship_type": "supersedes",
            "related_project_id": str(related.id),
            "notes": "Delete me",
        },
        headers=_auth_headers(),
    )
    relationship_id = created.json()["relationship_id"]

    response = client.delete(
        f"/projects/{project.id}/relationship/{relationship_id}",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    relationship = postgres_session.get(ProjectRelationship, uuid.UUID(relationship_id))
    change_logs = postgres_session.execute(
        select(ChangeLog)
        .where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "relationships",
        )
        .order_by(ChangeLog.timestamp)
    ).scalars().all()

    assert body["updated"] is True
    assert body["relationship_type"] == "supersedes"
    assert relationship is None
    assert change_logs[-1].old_value["relationship_type"] == "supersedes"
    assert change_logs[-1].old_value["notes"] == "Delete me"
    assert change_logs[-1].new_value is None


def test_delete_project_relationship_requires_outgoing_owner(
    postgres_session: Session,
) -> None:
    _ensure_relationship_api_tables(postgres_session)
    project = _project("946 OWNER RELATIONSHIP WAY LOS ANGELES CA 90012")
    related = _project("947 OWNER RELATED WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, related])
    postgres_session.flush()
    client = _client(postgres_session)
    created = client.post(
        f"/projects/{project.id}/relationship",
        json={"relationship_type": "phase", "related_project_id": str(related.id)},
        headers=_auth_headers(),
    )
    relationship_id = uuid.UUID(created.json()["relationship_id"])

    response = client.delete(
        f"/projects/{related.id}/relationship/{relationship_id}",
        headers=_auth_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Relationship not found."
    assert postgres_session.get(ProjectRelationship, relationship_id) is not None


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
