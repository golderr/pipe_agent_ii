from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser, AuthError
from tcg_pipeline.api.deps import get_db_session
from tcg_pipeline.api.main import create_app
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    Evidence,
    PipelineStatus,
    Project,
    ResearcherOverride,
    ReviewItem,
    ReviewItemType,
)
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.settings import Settings

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeVerifier:
    def __init__(self, error: AuthError | None = None) -> None:
        self.error = error

    def verify(self, token: str) -> AuthenticatedUser:
        if self.error is not None:
            raise self.error
        return AuthenticatedUser(
            user_id=USER_ID,
            email="allowed@example.com",
            role="authenticated",
            claims={"sub": str(USER_ID), "email": "allowed@example.com", "role": "authenticated"},
        )


def test_set_project_override_writes_table_resolves_and_logs(
    postgres_session: Session,
) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project("910 INLINE OVERRIDE WAY LOS ANGELES CA 90012", total_units=100)
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_total_units_evidence(project.id, 100))
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/override",
        json={
            "field_name": "total_units",
            "value": 212,
            "note": "Confirmed by researcher.",
            "source_url": "https://example.com/source",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    postgres_session.refresh(project)
    override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
            ResearcherOverride.cleared_at.is_(None),
        )
    ).scalar_one()
    change_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "total_units",
            ChangeLog.change_type == ChangeType.RESEARCHER_OVERRIDE,
        )
    ).scalar_one()

    assert body["field_name"] == "total_units"
    assert body["old_value"] == 100
    assert body["new_value"] == 212
    assert body["resolved_value"] == 212
    assert body["change_log_entries_created"] == 1
    assert project.total_units == 212
    assert project.last_editor == "allowed@example.com"
    assert override.value == 212
    assert override.set_by_user_id == USER_ID
    assert override.set_by_label == "allowed@example.com"
    assert override.mode == "review_protected"
    assert override.note == "Confirmed by researcher."
    assert override.source_url == "https://example.com/source"
    assert override.baseline is not None
    assert change_log.old_value == 100
    assert change_log.new_value == 212
    assert change_log.reviewed_by == "allowed@example.com"
    assert change_log.reviewed_by_user_id == USER_ID
    assert change_log.reviewed_by_email == "allowed@example.com"


def test_clear_project_override_marks_row_cleared_resolves_and_logs(
    postgres_session: Session,
) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project("911 CLEAR OVERRIDE WAY LOS ANGELES CA 90012", total_units=100)
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_total_units_evidence(project.id, 100))
    postgres_session.flush()
    client = _client(postgres_session)

    set_response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": "total_units", "value": 212},
        headers=_auth_headers(),
    )
    assert set_response.status_code == 200
    clear_response = client.delete(
        f"/projects/{project.id}/override/total_units",
        headers=_auth_headers(),
    )

    assert clear_response.status_code == 200
    body = clear_response.json()
    postgres_session.refresh(project)
    override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
        )
    ).scalar_one()
    change_log_values = postgres_session.execute(
        select(ChangeLog.old_value, ChangeLog.new_value)
        .where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "total_units",
            ChangeLog.change_type == ChangeType.RESEARCHER_OVERRIDE,
        )
        .order_by(ChangeLog.timestamp)
    ).all()

    assert body["cleared"] is True
    assert body["old_value"] == 212
    assert body["resolved_value"] == 100
    assert project.total_units == 100
    assert override.cleared_at is not None
    assert override.cleared_by_user_id == USER_ID
    assert change_log_values == [(100, 212), (212, 100)]


def test_set_project_override_rejects_non_core_field(postgres_session: Session) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project("912 INVALID FIELD WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": "rent_or_sale", "value": "Rental"},
        headers=_auth_headers(),
    )

    assert response.status_code == 400
    assert "not an editable evidence-derived field" in response.json()["detail"]


def test_set_project_override_rejects_invalid_value(postgres_session: Session) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project("913 INVALID VALUE WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": "total_units", "value": -1},
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "total_units must be a non-negative integer."


@pytest.mark.parametrize(
    ("field_name", "value", "expected"),
    [
        ("pipeline_status", "Approved", "Approved"),
        ("product_type", "Condo", "Condo"),
        ("age_restriction", "Senior", "Senior"),
        ("date_delivery", "2027-07-01", "2027-07-01"),
        ("developer", "  Example Development  ", "Example Development"),
        ("workforce_units", "12", 12),
    ],
)
def test_set_project_override_coerces_supported_core_values(
    postgres_session: Session,
    field_name: str,
    value: str,
    expected: Any,
) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project(f"914 {field_name} WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": field_name, "value": value},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == field_name,
            ResearcherOverride.cleared_at.is_(None),
        )
    ).scalar_one()
    assert override.value == expected


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("pipeline_status", "approved", "pipeline_status must be one of:"),
        ("product_type", "Mixed Use", "product_type must be one of:"),
        ("age_restriction", "Adults", "age_restriction must be one of:"),
        ("date_delivery", "2026/04/26", "date_delivery must be a YYYY-MM-DD date."),
        ("developer", "   ", "developer must be a non-empty string."),
        ("total_units", 212.7, "total_units must be a non-negative integer."),
        ("workforce_units", -1, "workforce_units must be a non-negative integer."),
    ],
)
def test_set_project_override_rejects_invalid_core_values(
    postgres_session: Session,
    field_name: str,
    value: Any,
    message: str,
) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project(f"915 INVALID {field_name} WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": field_name, "value": value},
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_small_delta_newer_evidence_against_manual_override_does_not_create_review_item(
    postgres_session: Session,
) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project("916 SMALL CONTRADICTION WAY LOS ANGELES CA 90012", total_units=100)
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_total_units_evidence(project.id, 100, record_suffix="baseline"))
    postgres_session.flush()
    client = _client(postgres_session)

    set_response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": "total_units", "value": 212},
        headers=_auth_headers(),
    )
    assert set_response.status_code == 200
    postgres_session.add(
        _total_units_evidence(
            project.id,
            216,
            record_suffix="newer",
            collected_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            evidence_date=date(2026, 5, 1),
        )
    )
    postgres_session.flush()

    resolve_project(project.id, postgres_session, apply=True, write_resolution_log=False)
    postgres_session.flush()
    postgres_session.refresh(project)
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one_or_none()

    assert project.total_units == 212
    assert review_item is None


def test_newer_evidence_against_manual_override_creates_review_item(
    postgres_session: Session,
) -> None:
    _ensure_override_api_tables(postgres_session)
    project = _project("916 CONTRADICTION WAY LOS ANGELES CA 90012", total_units=100)
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(_total_units_evidence(project.id, 100, record_suffix="baseline"))
    postgres_session.flush()
    client = _client(postgres_session)

    set_response = client.post(
        f"/projects/{project.id}/override",
        json={"field_name": "total_units", "value": 212},
        headers=_auth_headers(),
    )
    assert set_response.status_code == 200
    postgres_session.add(
        _total_units_evidence(
            project.id,
            260,
            record_suffix="newer",
            collected_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            evidence_date=date(2026, 5, 1),
        )
    )
    postgres_session.flush()

    resolve_project(project.id, postgres_session, apply=True, write_resolution_log=False)
    postgres_session.flush()
    postgres_session.refresh(project)
    review_item = postgres_session.execute(
        select(ReviewItem).where(
            ReviewItem.project_id == project.id,
            ReviewItem.item_type == ReviewItemType.OVERRIDE_CONTRADICTION,
        )
    ).scalar_one()

    assert project.total_units == 212
    assert review_item.priority.value == "medium"
    assert review_item.contradiction_priority == "medium"
    assert review_item.payload["field_name"] == "total_units"
    assert review_item.payload["current_override"]["value"] == 212
    assert review_item.payload["proposed_value"] == 260


def test_set_project_override_returns_404_for_missing_project(
    postgres_session: Session,
) -> None:
    _ensure_override_api_tables(postgres_session)
    client = _client(postgres_session)

    response = client.post(
        f"/projects/{uuid.uuid4()}/override",
        json={"field_name": "total_units", "value": 212},
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


def _ensure_override_api_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {"evidence", "researcher_overrides", "change_log", "resolution_log"}
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply the latest migrations before running override API tests: {missing}")


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


def _total_units_evidence(
    project_id: uuid.UUID,
    value: int,
    *,
    record_suffix: str | None = None,
    collected_at: datetime | None = None,
    evidence_date: date | None = None,
) -> Evidence:
    return Evidence(
        project_id=project_id,
        source_type="costar",
        source_tier=3,
        ingest_method="manual",
        source_record_id=f"costar-units-{project_id}-{record_suffix or value}",
        collected_at=collected_at or datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        evidence_date=evidence_date or date(2026, 4, 26),
        extracted_fields={"total_units": {"value": value, "confidence": "medium"}},
    )
