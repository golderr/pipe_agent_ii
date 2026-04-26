from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import require_user
from tcg_pipeline.api.errors import raise_not_implemented

router = APIRouter(prefix="/evidence", tags=["evidence"])
AUTH_USER = Depends(require_user)


@router.get("/{evidence_id}/snippet")
def get_evidence_snippet(
    evidence_id: uuid.UUID,
    field: str | None = None,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    suffix = f" for field {field}" if field else ""
    raise_not_implemented(f"evidence snippet for {evidence_id}{suffix}")
