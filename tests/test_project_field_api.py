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
    ProjectNote,
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


def test_update_project_field_writes_direct_field_and_logs(
    postgres_session: Session,
) -> None:
    _ensure_project_write_api_tables(postgres_session)
    project = _project(
        "920 DIRECT FIELD WAY LOS ANGELES CA 90012",
        project_name="Old Name",
    )
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/field",
        json={"field_name": "project_name", "value": "  New Name  "},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    postgres_session.refresh(project)
    change_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "project_name",
        )
    ).scalar_one()

    assert body["old_value"] == "Old Name"
    assert body["new_value"] == "New Name"
    assert body["change_log_entries_created"] == 1
    assert project.project_name == "New Name"
    assert project.last_editor == "allowed@example.com"
    assert change_log.source == "inline_field"
    assert change_log.change_type == ChangeType.RESEARCHER_CONFIRMED
    assert change_log.old_value == "Old Name"
    assert change_log.new_value == "New Name"


@pytest.mark.parametrize(
    ("field_name", "value", "expected"),
    [
        (
            "source_urls",
            "https://example.com/one\nhttps://example.com/two",
            ["https://example.com/one", "https://example.com/two"],
        ),
        ("previous_names", "Old One\nOld Two", ["Old One", "Old Two"]),
        ("inclusion_in_analysis", "No", False),
    ],
)
def test_update_project_field_coerces_supported_values(
    postgres_session: Session,
    field_name: str,
    value: Any,
    expected: Any,
) -> None:
    _ensure_project_write_api_tables(postgres_session)
    project = _project(f"921 {field_name} WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/field",
        json={"field_name": field_name, "value": value},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    postgres_session.refresh(project)
    assert getattr(project, field_name) == expected


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("researcher_notes", "Use notes endpoint", "project note endpoint"),
        ("source_urls", "not-a-url", "valid HTTP(S) URLs"),
        ("state", "California", "2-character postal abbreviation"),
    ],
)
def test_update_project_field_rejects_invalid_fields_or_values(
    postgres_session: Session,
    field_name: str,
    value: Any,
    message: str,
) -> None:
    _ensure_project_write_api_tables(postgres_session)
    project = _project(f"922 INVALID {field_name} WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/field",
        json={"field_name": field_name, "value": value},
        headers=_auth_headers(),
    )

    assert response.status_code in {400, 422}
    assert message in response.json()["detail"]


def test_append_project_note_creates_history_updates_latest_and_logs(
    postgres_session: Session,
) -> None:
    _ensure_project_write_api_tables(postgres_session)
    project = _project(
        "923 NOTE WAY LOS ANGELES CA 90012",
        researcher_notes="Initial note",
    )
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    first_response = client.post(
        f"/projects/{project.id}/note",
        json={"note_type": "researcher_notes", "body": " First appended note "},
        headers=_auth_headers(),
    )
    second_response = client.post(
        f"/projects/{project.id}/note",
        json={"note_type": "researcher_notes", "body": "Second appended note"},
        headers=_auth_headers(),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    postgres_session.refresh(project)
    note_rows = postgres_session.execute(
        select(ProjectNote)
        .where(ProjectNote.project_id == project.id)
        .order_by(ProjectNote.created_at, ProjectNote.id)
    ).scalars().all()
    change_log_rows = postgres_session.execute(
        select(ChangeLog)
        .where(ChangeLog.project_id == project.id, ChangeLog.field == "researcher_notes")
        .order_by(ChangeLog.timestamp, ChangeLog.id)
    ).scalars().all()

    assert project.researcher_notes == "Second appended note"
    assert [row.body for row in note_rows] == ["First appended note", "Second appended note"]
    assert all(row.created_by_user_id == USER_ID for row in note_rows)
    assert [row.old_value for row in change_log_rows] == ["Initial note", "First appended note"]
    assert [row.new_value for row in change_log_rows] == [
        "First appended note",
        "Second appended note",
    ]
    assert {row.source for row in change_log_rows} == {"project_note"}


def test_append_project_note_rejects_empty_body(postgres_session: Session) -> None:
    _ensure_project_write_api_tables(postgres_session)
    project = _project("924 EMPTY NOTE WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/note",
        json={"note_type": "researcher_notes", "body": "   "},
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert "note body must be a non-empty string" in response.json()["detail"]


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


def _ensure_project_write_api_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {"change_log", "project_notes"}
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(
            f"Apply the latest migrations before running project field API tests: {missing}"
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
