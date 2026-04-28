from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import (
    AuthenticatedUser,
    AuthError,
    TokenVerifier,
)
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.settings import Settings

bearer_auth = HTTPBearer(auto_error=False)
BEARER_CREDENTIALS = Depends(bearer_auth)


def get_jwt_verifier(request: Request) -> TokenVerifier:
    try:
        return request.app.state.jwt_verifier
    except AttributeError as exc:
        raise RuntimeError("JWT verifier missing from FastAPI app state.") from exc


def get_app_settings(request: Request) -> Settings:
    try:
        return request.app.state.settings
    except AttributeError as exc:
        raise RuntimeError("Settings missing from FastAPI app state.") from exc


JWT_VERIFIER = Depends(get_jwt_verifier)


def require_user(
    credentials: HTTPAuthorizationCredentials | None = BEARER_CREDENTIALS,
    verifier: TokenVerifier = JWT_VERIFIER,
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return verifier.verify(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        ) from exc


def get_db_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
