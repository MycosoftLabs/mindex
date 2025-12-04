from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return api_key


async def pagination_params(
    limit: Optional[int] = Query(
        None,
        ge=1,
        description="Maximum rows to return.",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Offset into the result set.",
    ),
) -> PaginationParams:
    if limit is None:
        limit = settings.default_page_size
    limit = min(limit, settings.max_page_size)
    return PaginationParams(limit=limit, offset=offset)
