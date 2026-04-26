from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session, require_user
from tcg_pipeline.db.models import Evidence
from tcg_pipeline.review.snippets import SnippetPayload, render_snippet

router = APIRouter(prefix="/evidence", tags=["evidence"])
AUTH_USER = Depends(require_user)
DB_SESSION = Depends(get_db_session)


@router.get("/{evidence_id}/snippet", response_model=SnippetPayload)
def get_evidence_snippet(
    evidence_id: uuid.UUID,
    field: str | None = None,
    _user: AuthenticatedUser = AUTH_USER,
    session: Session = DB_SESSION,
) -> SnippetPayload:
    evidence = session.execute(
        select(Evidence).where(Evidence.id == evidence_id)
    ).scalar_one_or_none()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence row not found.")
    return render_snippet(evidence, field_name=field)
