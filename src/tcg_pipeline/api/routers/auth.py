from __future__ import annotations

from fastapi import APIRouter, Depends

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import require_user
from tcg_pipeline.api.schemas import WhoAmIResponse

router = APIRouter(prefix="/auth", tags=["auth"])
AUTH_USER = Depends(require_user)


@router.get("/whoami", response_model=WhoAmIResponse)
def whoami(user: AuthenticatedUser = AUTH_USER) -> WhoAmIResponse:
    return WhoAmIResponse(
        user_id=user.user_id,
        email=user.email,
        role=user.role,
        actor_label=user.actor_label,
    )
