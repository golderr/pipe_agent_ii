from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from tcg_pipeline.api.schemas import HealthResponse, ReadyResponse
from tcg_pipeline.db.connection import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", app_env=request.app.state.settings.app_env)


@router.get("/readyz", response_model=ReadyResponse)
def readyz(request: Request) -> ReadyResponse:
    readiness_check = getattr(request.app.state, "readiness_check", None)
    try:
        if readiness_check is not None:
            readiness_check()
        else:
            _check_database_ready()
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ReadyResponse(status="ok", database="ok")


def _check_database_ready() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
