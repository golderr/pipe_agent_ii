from __future__ import annotations

import uuid

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app_env: str


class ReadyResponse(BaseModel):
    status: str
    database: str


class WhoAmIResponse(BaseModel):
    user_id: uuid.UUID
    email: str | None
    role: str
    actor_label: str
