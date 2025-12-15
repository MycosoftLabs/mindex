from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db

api_key_header = APIKeyHeader(name='X-API-Key', auto_error=False)


@dataclass
class PaginationParams:
    limit: int
    offset: int


async def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


async def require_api_key(api_key: Optional[str] = Depends(api_key_header)) -> Optional[str]:
    """Simple API key guard for non-health routes."""
    if settings.api_keys:
        if api_key is None or api_key not in settings.api_keys:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
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
