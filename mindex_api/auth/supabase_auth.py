"""
Supabase user-JWT authentication for the MINDEX API.

Mirrors the pattern of `internal_auth.py` and `api_key_auth.py`: exposes
FastAPI dependencies that resolve a request's identity into a
`CallerIdentity` consumable by the rest of the API.

Verification strategy: the bearer token from `Authorization: Bearer <jwt>`
is sent to Supabase's `/auth/v1/user` endpoint, which validates the JWT
server-side and returns the user payload. This avoids shipping the
project's JWT signing secret to MINDEX hosts at the cost of one round
trip per request — fine for the current request volumes; swap to local
JWT verification (PyJWT + JWKS) when latency matters.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import settings
from .models import CallerIdentity

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

_USER_ENDPOINT = "/auth/v1/user"
_USER_FETCH_TIMEOUT_SECONDS = 5.0


def _supabase_configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_anon_key)


def _parse_uuid(value: object) -> uuid.UUID:
    """Return a UUID for the given Supabase id, falling back to a stable zero UUID."""
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            pass
    return uuid.UUID(int=0)


async def _fetch_supabase_user(token: str) -> Optional[dict]:
    """Resolve a bearer JWT to a Supabase user payload, or None if invalid."""
    if not _supabase_configured():
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": settings.supabase_anon_key or "",
    }
    url = f"{settings.supabase_url.rstrip('/')}{_USER_ENDPOINT}"

    try:
        async with httpx.AsyncClient(timeout=_USER_FETCH_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Supabase user lookup failed: %s", exc)
        return None

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code in (401, 403):
        return None

    logger.warning(
        "Unexpected status from Supabase user endpoint: %s %s",
        resp.status_code,
        resp.text[:200],
    )
    return None


def _user_to_identity(user: dict) -> CallerIdentity:
    app_metadata = user.get("app_metadata") or {}
    plan = app_metadata.get("plan") or "supabase"
    raw_scopes = app_metadata.get("roles") or app_metadata.get("scopes") or []
    scopes = [str(s) for s in raw_scopes] if isinstance(raw_scopes, (list, tuple)) else []

    return CallerIdentity(
        key_id=uuid.UUID(int=0),
        owner_id=_parse_uuid(user.get("id")),
        user_type="human",
        plan=plan,
        scopes=scopes,
        rate_limit_per_minute=60,
        rate_limit_per_day=10_000,
        service="supabase",
    )


async def require_supabase_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CallerIdentity:
    """FastAPI dependency: 401s unless a valid Supabase user JWT is present."""
    if not _supabase_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase auth is not configured on this server.",
        )

    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Supabase bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await _fetch_supabase_user(credentials.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Supabase token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _user_to_identity(user)


async def optional_supabase_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[CallerIdentity]:
    """FastAPI dependency: returns a CallerIdentity if a valid token is present, else None."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        return None
    if not _supabase_configured():
        return None

    user = await _fetch_supabase_user(credentials.credentials)
    if user is None:
        return None
    return _user_to_identity(user)
