from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tcg_pipeline.api.auth import AuthenticatedUser, AuthError, _is_email_allowed
from tcg_pipeline.api.deps import get_db_session
from tcg_pipeline.api.main import create_app
from tcg_pipeline.api.routers import coverage as coverage_router
from tcg_pipeline.api.routers import review as review_router
from tcg_pipeline.db.models import (
    CoStarUploadStatus,
    Priority,
    ReviewItemStatus,
    ReviewItemType,
    ScrapeJobStatus,
    ScrapeTriggerType,
)
from tcg_pipeline.db.review_workflow import ReviewItemAlreadyStagedError, ReviewStageResult
from tcg_pipeline.settings import Settings

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ITEM_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
JURISDICTION_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
JOB_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
EVIDENCE_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
UPLOAD_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")


class FakeVerifier:
    def __init__(self, error: AuthError | None = None) -> None:
        self.error = error
        self.tokens: list[str] = []

    def verify(self, token: str) -> AuthenticatedUser:
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return AuthenticatedUser(
            user_id=USER_ID,
            email="allowed@example.com",
            role="authenticated",
            claims={"sub": str(USER_ID), "email": "allowed@example.com", "role": "authenticated"},
        )


def _settings() -> Settings:
    return Settings(
        app_env="test",
        database_url=None,
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        allowed_emails="allowed@example.com",
        api_cors_origins="http://localhost:3000,https://tcg-pipeline.vercel.app",
    )


def _client(
    *,
    verifier: FakeVerifier | None = None,
    readiness_check: Callable[[], None] | None = None,
) -> TestClient:
    app = create_app(
        settings=_settings(),
        jwt_verifier=verifier or FakeVerifier(),
        readiness_check=readiness_check or (lambda: None),
    )
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer valid-token"}


def test_health_and_readiness_endpoints_do_not_require_auth() -> None:
    client = _client()

    health = client.get("/healthz")
    ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "app_env": "test"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "ok"}


def test_readiness_returns_503_when_check_fails() -> None:
    def fail_ready() -> None:
        raise RuntimeError("database unavailable")

    client = _client(readiness_check=fail_ready)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["detail"] == "database unavailable"


def test_cors_allows_configured_frontend_origin() -> None:
    client = _client()

    response = client.options(
        "/auth/whoami",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("app_env", ["development", "preview", "staging", "production"])
def test_empty_allowed_emails_fails_closed(app_env: str) -> None:
    settings = Settings(app_env=app_env, allowed_emails="")

    assert _is_email_allowed("allowed@example.com", settings) is False


def test_whoami_requires_bearer_token() -> None:
    client = _client()

    response = client.get("/auth/whoami")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."


def test_whoami_returns_verified_user() -> None:
    verifier = FakeVerifier()
    client = _client(verifier=verifier)

    response = client.get("/auth/whoami", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(USER_ID),
        "email": "allowed@example.com",
        "role": "authenticated",
        "actor_label": "allowed",
    }
    assert verifier.tokens == ["valid-token"]


def test_auth_errors_are_mapped_to_http_responses() -> None:
    client = _client(verifier=FakeVerifier(AuthError(403, "Email is not allowed.")))

    response = client.get("/auth/whoami", headers=_auth_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "Email is not allowed."


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/projects/{PROJECT_ID}"),
        ("POST", f"/coverage/{JURISDICTION_ID}/pin"),
    ],
)
def test_phase_c_routes_are_protected_stubs(method: str, path: str) -> None:
    client = _client()
    request_kwargs: dict[str, Any] = {"headers": _auth_headers()}
    if method == "POST":
        request_kwargs["json"] = {}

    response = client.request(method, path, **request_kwargs)

    assert response.status_code == 501
    assert response.json()["detail"]["phase"] == "C.a"


@pytest.mark.parametrize(
    "path",
    [
        f"/projects/{PROJECT_ID}/field",
        f"/projects/{PROJECT_ID}/note",
        f"/projects/{PROJECT_ID}/relationship",
        "/projects",
        f"/review/{ITEM_ID}/decide",
        f"/review/{ITEM_ID}/revise",
        f"/coverage/{JURISDICTION_ID}/scrape",
    ],
)
def test_phase_c_project_write_routes_are_implemented_and_body_validated(path: str) -> None:
    client = _client()

    response = client.post(path, json={}, headers=_auth_headers())

    assert response.status_code == 422


def test_costar_upload_route_requires_multipart_file() -> None:
    client = _client()

    response = client.post(f"/coverage/{JURISDICTION_ID}/costar-upload", headers=_auth_headers())

    assert response.status_code == 422


def test_coverage_scrape_enqueue_uses_full_actor_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    calls: dict[str, Any] = {}
    background_job_ids: list[uuid.UUID] = []

    class FakeSession:
        def __init__(self) -> None:
            self.commit_calls = 0

        def commit(self) -> None:
            self.commit_calls += 1

    fake_session = FakeSession()
    client.app.dependency_overrides[get_db_session] = lambda: fake_session

    def fake_enqueue_scrape_job(_session: object, **kwargs: Any) -> object:
        calls.update(kwargs)
        return _fake_scrape_job()

    monkeypatch.setattr(coverage_router, "enqueue_scrape_job", fake_enqueue_scrape_job)
    monkeypatch.setattr(
        coverage_router,
        "run_scrape_job",
        lambda job_id: background_job_ids.append(job_id),
    )

    response = client.post(
        f"/coverage/{JURISDICTION_ID}/scrape",
        json={"source_name": "ladbs_permits"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(JOB_ID)
    assert response.json()["status"] == "queued"
    assert calls["jurisdiction_id"] == JURISDICTION_ID
    assert calls["source_name"] == "ladbs_permits"
    assert calls["user"].user_id == USER_ID
    assert calls["user"].email == "allowed@example.com"
    assert fake_session.commit_calls == 1
    assert background_job_ids == [JOB_ID]


def test_scrape_job_status_serializes_job() -> None:
    client = _client()

    class FakeSession:
        def get(self, _model: object, job_id: uuid.UUID) -> object | None:
            return _fake_scrape_job() if job_id == JOB_ID else None

    client.app.dependency_overrides[get_db_session] = lambda: FakeSession()

    response = client.get(f"/scrape_jobs/{JOB_ID}", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["id"] == str(JOB_ID)
    assert response.json()["source_name"] == "ladbs_permits"
    assert response.json()["progress"] == {"message": "Queued for API background scrape."}


def test_scrape_worker_health_reports_unconfigured_queue() -> None:
    client = _client()

    response = client.get("/scrape_workers/health", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["available"] is False
    assert response.json()["queue_name"] == "scrape_jobs"


def test_costar_upload_uses_full_actor_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.app.dependency_overrides[get_db_session] = lambda: object()
    calls: dict[str, Any] = {}

    def fake_process_costar_upload(_session: object, **kwargs: Any) -> object:
        calls.update(kwargs)
        return SimpleNamespace(
            id=UPLOAD_ID,
            jurisdiction_id=kwargs["jurisdiction_id"],
            file_name=kwargs["upload_file"].filename,
            file_size_bytes=11,
            row_count=2,
            source_run_id=None,
            status=CoStarUploadStatus.COMPLETED,
            error_text=None,
        )

    monkeypatch.setattr(coverage_router, "process_costar_upload", fake_process_costar_upload)

    response = client.post(
        f"/coverage/{JURISDICTION_ID}/costar-upload",
        files={
            "file": (
                "costar.xlsx",
                b"fake xlsx bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(UPLOAD_ID)
    assert response.json()["status"] == "completed"
    assert calls["jurisdiction_id"] == JURISDICTION_ID
    assert calls["user"].user_id == USER_ID
    assert calls["user"].email == "allowed@example.com"
    assert calls["upload_file"].filename == "costar.xlsx"


def test_review_decide_stages_with_full_actor_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.app.dependency_overrides[get_db_session] = lambda: object()
    calls: dict[str, Any] = {}

    def fake_stage_review_decision(_session: object, **kwargs: Any) -> ReviewStageResult:
        calls.update(kwargs)
        return ReviewStageResult(
            review_item_id=ITEM_ID,
            decision_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
            decision_type=kwargs["decision_type"],
            item_state="staged",
            staged_by=kwargs["staged_by"],
            staged_by_email=kwargs["staged_by_email"],
        )

    monkeypatch.setattr(
        review_router,
        "stage_review_decision",
        fake_stage_review_decision,
    )

    response = client.post(
        f"/review/{ITEM_ID}/decide",
        json={
            "decision_type": "custom",
            "decision_value": {"value": 30},
            "notes": "Confirmed.",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["staged_by"] == str(USER_ID)
    assert response.json()["staged_by_email"] == "allowed@example.com"
    assert calls["staged_by"] == USER_ID
    assert calls["staged_by_email"] == "allowed@example.com"
    assert calls["decision_value"] == {"value": 30}


def test_review_decide_returns_409_for_competing_staged_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    client.app.dependency_overrides[get_db_session] = lambda: object()
    other_user_id = uuid.UUID("88888888-8888-8888-8888-888888888888")

    def fake_stage_review_decision(_session: object, **_kwargs: Any) -> ReviewStageResult:
        raise ReviewItemAlreadyStagedError(
            review_item_id=ITEM_ID,
            staged_by=other_user_id,
            staged_by_email="other@example.com",
            decision_type="keep_old",
            staged_at=None,
        )

    monkeypatch.setattr(
        review_router,
        "stage_review_decision",
        fake_stage_review_decision,
    )

    response = client.post(
        f"/review/{ITEM_ID}/decide",
        json={"decision_type": "custom", "decision_value": {"value": 30}},
        headers=_auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["staged_by"] == str(other_user_id)
    assert response.json()["detail"]["staged_by_email"] == "other@example.com"
    assert response.json()["detail"]["decision_type"] == "keep_old"


def test_review_queue_serializes_committed_decision_for_reviewed_tab() -> None:
    decision_id = uuid.UUID("77777777-7777-7777-7777-777777777777")
    committed_at = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    review_item = SimpleNamespace(
        id=ITEM_ID,
        project_id=PROJECT_ID,
        source_run_id=None,
        item_type=ReviewItemType.OVERRIDE_CONTRADICTION,
        status=ReviewItemStatus.ACCEPTED,
        state="committed",
        priority=Priority.MEDIUM,
        match_confidence=None,
        field_name="total_units",
        winning_evidence_id=None,
        payload={"field_name": "total_units"},
        assigned_to=None,
        created_at=datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
        resolved_at=committed_at,
        resolved_by="allowed@example.com",
        decisions=[
            SimpleNamespace(
                id=decision_id,
                state="committed",
                decision_type="accept_new",
                staged_at=datetime(2026, 4, 28, 11, 0, tzinfo=UTC),
                staged_by=USER_ID,
                staged_by_email="allowed@example.com",
                committed_at=committed_at,
                committed_by=USER_ID,
                committed_by_email="allowed@example.com",
                decision_value=None,
                decision_notes="Confirmed.",
                source_url=None,
                created_at=datetime(2026, 4, 28, 11, 0, tzinfo=UTC),
            )
        ],
    )

    serialized = review_router._serialize_review_item(review_item)

    assert serialized.state == "committed"
    assert serialized.field_name == "total_units"
    assert serialized.winning_evidence_id is None
    assert serialized.evidence_summaries == []
    assert serialized.active_decision is not None
    assert serialized.active_decision.decision_id == decision_id
    assert serialized.active_decision.state == "committed"
    assert serialized.active_decision.decision_type == "accept_new"
    assert serialized.active_decision.committed_by == USER_ID
    assert serialized.active_decision.committed_by_email == "allowed@example.com"


def test_phase_c_stubs_do_not_run_without_auth() -> None:
    client = _client()

    response = client.post(f"/projects/{PROJECT_ID}/override", json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."


def _fake_scrape_job() -> object:
    return SimpleNamespace(
        id=JOB_ID,
        jurisdiction_id=JURISDICTION_ID,
        source_name="ladbs_permits",
        trigger_type=ScrapeTriggerType.USER_INITIATED,
        initiated_by_user_id=USER_ID,
        initiated_by_email="allowed@example.com",
        status=ScrapeJobStatus.QUEUED,
        queued_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        started_at=None,
        completed_at=None,
        source_run_id=None,
        error_text=None,
        progress={"message": "Queued for API background scrape."},
    )


def test_evidence_snippet_requires_auth_before_database_access() -> None:
    client = _client()

    response = client.get(f"/evidence/{EVIDENCE_ID}/snippet?field=total_units")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."
