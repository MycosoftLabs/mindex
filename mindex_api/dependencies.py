from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db

api_key_header = APIKeyHeader(name='X-API-Key', auto_error=False)
internal_token_header = APIKeyHeader(name='X-Internal-Token', auto_error=False)


def _internal_tokens() -> list[str]:
    import os

    raw = os.environ.get('MINDEX_INTERNAL_TOKENS', '') or os.environ.get('MINDEX_INTERNAL_TOKEN', '')
    return [t.strip() for t in raw.split(',') if t.strip()]


@dataclass
class PaginationParams:
    limit: int
    offset: int


async def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


def _is_production() -> bool:
    import os

    env = (os.environ.get("MINDEX_ENV") or os.environ.get("ENVIRONMENT") or "").strip().lower()
    return env in ("production", "prod")


async def require_api_key(
    api_key: Optional[str] = Depends(api_key_header),
    internal_token: Optional[str] = Depends(internal_token_header),
) -> Optional[str]:
    """Simple API key guard for non-health routes.

    Accepts either X-API-Key (API_KEYS env) or X-Internal-Token
    (MINDEX_INTERNAL_TOKENS env) so internal callers keep working when
    API_KEYS enforcement is enabled.

    Fail-closed in production: if API_KEYS is unset/empty while
    MINDEX_ENV/ENVIRONMENT is production, requests are rejected instead of
    silently allowing everything (security audit JUN09_2026, finding X-1).

    DEPRECATED: This checks against the flat API_KEYS env var.
    For Worldview endpoints, use auth.require_worldview_key instead.
    For internal endpoints, use auth.require_internal_token instead.
    Both validate against the api_keys DB table with full identity context.
    """
    import logging

    if internal_token and internal_token in _internal_tokens():
        return internal_token

    if not settings.api_keys:
        if _is_production():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='API_KEYS not configured; authenticated routes disabled',
            )
        logging.getLogger(__name__).warning(
            "API_KEYS unset — require_api_key is OPEN (non-production only)"
        )
        return api_key

    if api_key is None or api_key not in settings.api_keys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    logging.getLogger(__name__).debug(
        "Legacy env-var API key auth used — migrate to DB-backed auth "
        "(auth.require_worldview_key or auth.require_internal_token)"
    )
    return api_key


async def require_device_api_key(
    api_key: Optional[str] = Depends(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> Optional[str]:
    """
    Device API key authentication.

    - If API_KEYS is configured and the provided key matches, it is accepted.
    - Otherwise, allow per-device keys for MycoBrain V1 by matching SHA-256(api_key)
      against telemetry.device.api_key_hash.
    """
    import hashlib

    from sqlalchemy import text

    if api_key is None:
        if settings.api_keys:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
        if _is_production():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='API_KEYS not configured; device routes disabled',
            )
        return None

    if settings.api_keys and api_key in settings.api_keys:
        return api_key

    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    stmt = text(
        """
        SELECT 1
        FROM telemetry.device
        WHERE device_type = 'mycobrain_v1'
          AND api_key_hash = :api_key_hash
        LIMIT 1
        """
    )
    result = await db.execute(stmt, {'api_key_hash': api_key_hash})
    if result.scalar_one_or_none():
        return api_key

    if settings.api_keys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

    return api_key


async def pagination_params(
    limit: Optional[int] = Query(
        None,
        ge=1,
        description='Maximum rows to return.',
    ),
    offset: int = Query(
        0,
        ge=0,
        description='Offset into the result set.',
    ),
) -> PaginationParams:
    if limit is None:
        limit = settings.default_page_size
    limit = min(limit, settings.max_page_size)
    return PaginationParams(limit=limit, offset=offset)
