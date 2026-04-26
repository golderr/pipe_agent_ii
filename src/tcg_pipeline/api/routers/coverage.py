from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import require_user
from tcg_pipeline.api.errors import raise_not_implemented

router = APIRouter(tags=["coverage"])
AUTH_USER = Depends(require_user)
JSON_BODY = Body(default_factory=dict)


@router.post("/coverage/{jurisdiction_id}/pin")
def toggle_jurisdiction_pin(
    jurisdiction_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"jurisdiction pin toggle for {jurisdiction_id}")


@router.post("/coverage/{jurisdiction_id}/scrape")
def enqueue_scrape(
    jurisdiction_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"scrape kickoff for {jurisdiction_id}")


@router.post("/coverage/{jurisdiction_id}/costar-upload")
def upload_costar_export(
    jurisdiction_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"CoStar upload for {jurisdiction_id}")


@router.get("/scrape_jobs/{job_id}")
def get_scrape_job(
    job_id: uuid.UUID,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"scrape job status for {job_id}")
