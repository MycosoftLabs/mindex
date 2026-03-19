"""
DB-backed API key authentication for the Worldview API.

Validates X-API-Key header against the api_keys table (SHA-256 hashed),
joins to user_registry for identity, and caches lookups in Redis.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from .models import CallerIdentity

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# In-process cache for key lookups (avoids DB hit on every request)
_identity_cache: dict[str, tuple[CallerIdentity, float]] = {}
_CACHE_TTL = 60.0  # seconds


def _get_cached_identity(key_hash: str) -> Optional[CallerIdentity]:
    """Check in-process cache for a recently validated key."""
    import time

    entry = _identity_cache.get(key_hash)
    if entry is None:
        return None
    identity, cached_at = entry
    if time.time() - cached_at > _CACHE_TTL:
        _identity_cache.pop(key_hash, None)
        return None
    return identity


def _cache_identity(key_hash: str, identity: CallerIdentity) -> None:
    import time

    # Evict oldest if cache is too large
    if len(_identity_cache) > 5000:
        oldest_key = min(_identity_cache, key=lambda k: _identity_cache[k][1])
        _identity_cache.pop(oldest_key, None)
    _identity_cache[key_hash] = (identity, time.time())


async def require_worldview_key(
    api_key: Optional[str] = Depends(_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> CallerIdentity:
    """
    Validate an API key against the api_keys DB table for Worldview access.

    Returns CallerIdentity on success, raises 401/403 on failure.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide X-API-Key header.",
        )

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # Check in-process cache first
    cached = _get_cached_identity(key_hash)
    if cached is not None:
        return cached

    # Query DB: api_keys JOIN user_registry
    stmt = text("""
        SELECT
            ak.id,
            ak.user_id,
            ak.service,
            ak.scopes,
            ak.rate_limit_per_minute,
            ak.rate_limit_per_day,
            ak.is_active,
            ak.expires_at,
            ur.user_type,
            ur.plan_tier,
            ur.startup_fee_paid,
            ur.payment_status
        FROM api_keys ak
        LEFT JOIN user_registry ur ON ur.id = ak.user_id
        WHERE ak.key_hash = :key_hash
        LIMIT 1
    """)

    try:
        result = await db.execute(stmt, {"key_hash": key_hash})
        row = result.fetchone()
    except Exception as e:
        logger.error(f"API key lookup failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable.",
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    (
        key_id, user_id, service, scopes, rate_per_min, rate_per_day,
        is_active, expires_at, user_type, plan_tier,
        startup_fee_paid, payment_status,
    ) = row

    if not is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key has been deactivated.",
        )

    if expires_at is not None:
        from datetime import datetime, timezone

        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key has expired.",
            )

    # Check that key has worldview access
    scopes_list = scopes if isinstance(scopes, list) else []
    if service != "worldview" and "worldview:read" not in scopes_list:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key does not have Worldview API access.",
        )

    identity = CallerIdentity(
        key_id=key_id if isinstance(key_id, uuid.UUID) else uuid.UUID(str(key_id)),
        owner_id=user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id)) if user_id else uuid.UUID(int=0),
        user_type=user_type or "human",
        plan=plan_tier or "free",
        scopes=scopes_list,
        rate_limit_per_minute=rate_per_min or 60,
        rate_limit_per_day=rate_per_day or 10000,
        service=service or "worldview",
    )

    _cache_identity(key_hash, identity)
    return identity
