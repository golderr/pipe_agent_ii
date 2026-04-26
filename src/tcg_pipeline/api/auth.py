from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt
from jwt import PyJWKClient, PyJWKClientError
from jwt.exceptions import InvalidTokenError

from tcg_pipeline.settings import Settings

SUPPORTED_JWKS_ALGORITHMS = ("RS256", "ES256", "EdDSA")


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: uuid.UUID
    email: str | None
    role: str
    claims: Mapping[str, Any]

    @property
    def actor_label(self) -> str:
        if self.email:
            return self.email.split("@", 1)[0]
        return str(self.user_id)


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AuthenticatedUser:
        ...


class AuthError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _JWKSUnavailableError(Exception):
    pass


class SupabaseJWTVerifier:
    """Verify Supabase Auth access tokens for the API write boundary."""

    def __init__(self, settings: Settings, *, http_client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http_client = http_client
        self._jwks_client: PyJWKClient | None = None

    def verify(self, token: str) -> AuthenticatedUser:
        token = token.strip()
        if not token:
            raise AuthError(401, "Missing bearer token.")

        try:
            claims = self._verify_with_jwks(token)
        except _JWKSUnavailableError:
            claims = self._verify_with_auth_server(token)

        return self._user_from_claims(claims)

    def _verify_with_jwks(self, token: str) -> Mapping[str, Any]:
        if not self._settings.supabase_url:
            raise _JWKSUnavailableError("SUPABASE_URL is not configured.")

        try:
            signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
        except PyJWKClientError as exc:
            raise _JWKSUnavailableError(str(exc)) from exc

        try:
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=list(SUPPORTED_JWKS_ALGORITHMS),
                audience=self._settings.api_auth_audience,
                issuer=self._issuer,
            )
        except InvalidTokenError as exc:
            raise AuthError(401, "Invalid access token.") from exc

    def _verify_with_auth_server(self, token: str) -> Mapping[str, Any]:
        if not self._settings.supabase_url or not self._settings.supabase_anon_key:
            raise AuthError(401, "Unable to verify access token.")

        headers = {
            "apikey": self._settings.supabase_anon_key,
            "Authorization": f"Bearer {token}",
        }
        client = self._http_client or httpx.Client(timeout=5.0)
        close_client = self._http_client is None
        try:
            user_url = f"{self._settings.supabase_url.rstrip('/')}/auth/v1/user"
            response = client.get(user_url, headers=headers)
        except httpx.HTTPError as exc:
            raise AuthError(401, "Unable to verify access token.") from exc
        finally:
            if close_client:
                client.close()

        if response.status_code != 200:
            raise AuthError(401, "Invalid access token.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthError(401, "Invalid access token.") from exc

        return {
            "sub": payload.get("id"),
            "email": payload.get("email"),
            "role": payload.get("role")
            or payload.get("aud")
            or self._settings.api_required_role,
            "app_metadata": payload.get("app_metadata"),
            "user_metadata": payload.get("user_metadata"),
        }

    def _user_from_claims(self, claims: Mapping[str, Any]) -> AuthenticatedUser:
        raw_user_id = claims.get("sub")
        if not isinstance(raw_user_id, str):
            raise AuthError(401, "Access token is missing a user id.")

        try:
            user_id = uuid.UUID(raw_user_id)
        except ValueError as exc:
            raise AuthError(401, "Access token has an invalid user id.") from exc

        role = claims.get("role")
        # Supabase JWT `aud` and `role` are separate claims. The JWT decoder
        # validates audience; this role check keeps service/admin tokens out.
        if role != self._settings.api_required_role:
            raise AuthError(403, "Access token role is not allowed.")

        email = claims.get("email")
        email_text = email if isinstance(email, str) and email.strip() else None
        if not _is_email_allowed(email_text, self._settings):
            raise AuthError(403, "Email is not allowed.")

        return AuthenticatedUser(
            user_id=user_id,
            email=email_text,
            role=str(role),
            claims=claims,
        )

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                f"{self._settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json",
                lifespan=self._settings.api_jwks_cache_ttl_seconds,
            )
        return self._jwks_client

    @property
    def _issuer(self) -> str:
        return f"{self._settings.supabase_url.rstrip('/')}/auth/v1"


def _allowed_email_set(settings: Settings) -> set[str]:
    return {
        email.strip().lower()
        for email in (settings.allowed_emails or "").split(",")
        if email.strip()
    }


def _is_email_allowed(email: str | None, settings: Settings) -> bool:
    if not email:
        return False
    allowlist = _allowed_email_set(settings)
    if not allowlist:
        return False
    return email.lower() in allowlist
