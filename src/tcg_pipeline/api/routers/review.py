from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import require_user
from tcg_pipeline.api.errors import raise_not_implemented

router = APIRouter(prefix="/review", tags=["review"])
AUTH_USER = Depends(require_user)
JSON_BODY = Body(default_factory=dict)


@router.get("/queue")
def list_review_queue(_user: AuthenticatedUser = AUTH_USER) -> None:
    raise_not_implemented("review queue listing")


@router.get("/queue/{item_id}")
def get_review_item(
    item_id: uuid.UUID,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"review item detail for {item_id}")


@router.post("/{item_id}/decide")
def decide_review_item(
    item_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"stage review decision for {item_id}")


@router.post("/{item_id}/revise")
def revise_review_item(
    item_id: uuid.UUID,
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"revise staged review decision for {item_id}")


@router.post("/{item_id}/unstage")
def unstage_review_item(
    item_id: uuid.UUID,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented(f"unstage review decision for {item_id}")


@router.post("/commit")
def commit_review_decisions(
    _payload: dict[str, Any] = JSON_BODY,
    _user: AuthenticatedUser = AUTH_USER,
) -> None:
    raise_not_implemented("commit staged review decisions")
