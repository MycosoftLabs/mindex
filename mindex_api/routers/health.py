from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db_session)) -> HealthResponse:
    """Basic liveness and DB connectivity check."""
    db_state = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_state = "error"
    status = "ok" if db_state == "ok" else "degraded"
    return HealthResponse(status=status, db=db_state, timestamp=datetime.now(timezone.utc))
