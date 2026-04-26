from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tcg_pipeline.api.auth import AuthenticatedUser, AuthError, _is_email_allowed
from tcg_pipeline.api.main import create_app
from tcg_pipeline.settings import Settings

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ITEM_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
JURISDICTION_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
JOB_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
EVIDENCE_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")


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
        ("POST", "/projects"),
        ("GET", f"/projects/{PROJECT_ID}"),
        ("POST", f"/projects/{PROJECT_ID}/field"),
        ("POST", f"/projects/{PROJECT_ID}/override"),
        ("DELETE", f"/projects/{PROJECT_ID}/override/total_units"),
        ("POST", f"/projects/{PROJECT_ID}/note"),
        ("POST", f"/projects/{PROJECT_ID}/relationship"),
        ("GET", "/review/queue"),
        ("GET", f"/review/queue/{ITEM_ID}"),
        ("POST", f"/review/{ITEM_ID}/decide"),
        ("POST", f"/review/{ITEM_ID}/revise"),
        ("POST", f"/review/{ITEM_ID}/unstage"),
        ("POST", "/review/commit"),
        ("POST", f"/coverage/{JURISDICTION_ID}/pin"),
        ("POST", f"/coverage/{JURISDICTION_ID}/scrape"),
        ("POST", f"/coverage/{JURISDICTION_ID}/costar-upload"),
        ("GET", f"/scrape_jobs/{JOB_ID}"),
        ("GET", f"/evidence/{EVIDENCE_ID}/snippet?field=total_units"),
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


def test_phase_c_stubs_do_not_run_without_auth() -> None:
    client = _client()

    response = client.post(f"/projects/{PROJECT_ID}/override", json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."
