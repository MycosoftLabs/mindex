from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..contracts.v1.health import HealthResponse, VersionResponse
from ..config import settings

router = APIRouter(tags=["health"])


def _get_git_sha() -> str | None:
    """Get current git commit SHA."""
    # First check environment variable (set during Docker build)
    git_sha = os.environ.get("GIT_SHA")
    if git_sha:
        return git_sha
    # Try to get from git command
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db_session)) -> HealthResponse:
    """Basic liveness and DB connectivity check."""
    db_state = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_state = "error"
    status = "ok" if db_state == "ok" else "degraded"
    return HealthResponse(status=status, db=db_state, timestamp=datetime.now(timezone.utc), service="mindex", version=settings.api_version, git_sha=_get_git_sha())


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Return service version information."""
    return VersionResponse(
        service="mindex",
        version=settings.api_version,
        git_sha=_get_git_sha(),
    )


@router.get("/health/detailed")
async def detailed_health_check(db: AsyncSession = Depends(get_db_session)) -> dict:
    """
    Detailed health check including DB, Redis, auth tables, and API zones.

    Checks:
    - Database connectivity
    - Redis connectivity (if configured)
    - user_registry table accessibility
    - api_keys table accessibility
    - API zone status (internal, worldview, utility)
    """
    checks: dict = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "mindex",
        "version": settings.api_version,
        "git_sha": _get_git_sha(),
        "checks": {},
        "zones": {
            "internal": {"prefix": settings.internal_prefix, "auth": "X-Internal-Token (HMAC)"},
            "worldview": {"prefix": settings.worldview_prefix, "auth": "X-API-Key (DB-backed)"},
            "utility": {"prefix": settings.api_prefix, "auth": "open/light"},
        },
    }

    # DB check
    try:
        await db.execute(text("SELECT 1"))
        checks["checks"]["database"] = "ok"
    except Exception as e:
        checks["checks"]["database"] = f"error: {str(e)[:100]}"
        checks["status"] = "degraded"

    # Redis check
    try:
        if settings.redis_url:
            import redis.asyncio as aioredis
            r = aioredis.from_url(settings.redis_url)
            await r.ping()
            await r.aclose()
            checks["checks"]["redis"] = "ok"
        else:
            checks["checks"]["redis"] = "not_configured (using in-process LRU)"
    except Exception as e:
        checks["checks"]["redis"] = f"error: {str(e)[:100]}"

    # user_registry table check
    try:
        result = await db.execute(text("SELECT COUNT(*) FROM user_registry"))
        count = result.scalar()
        checks["checks"]["user_registry"] = f"ok ({count} users)"
    except Exception:
        checks["checks"]["user_registry"] = "not_available (migration pending)"

    # api_keys table check
    try:
        result = await db.execute(text(
            "SELECT COUNT(*) FILTER (WHERE is_active), COUNT(*) FROM api_keys"
        ))
        row = result.fetchone()
        active, total = (row[0], row[1]) if row else (0, 0)
        checks["checks"]["api_keys"] = f"ok ({active} active / {total} total)"
    except Exception:
        checks["checks"]["api_keys"] = "not_available (migration pending)"

    # api_key_usage table check
    try:
        result = await db.execute(text("SELECT COUNT(*) FROM api_key_usage"))
        count = result.scalar()
        checks["checks"]["api_key_usage"] = f"ok ({count} records)"
    except Exception:
        checks["checks"]["api_key_usage"] = "not_available"

    # Internal auth config check
    has_internal_secret = bool(settings.internal_auth_secret)
    has_internal_tokens = bool(settings.internal_tokens)
    if has_internal_secret or has_internal_tokens:
        checks["checks"]["internal_auth"] = "configured"
    else:
        checks["checks"]["internal_auth"] = "not_configured (set MINDEX_INTERNAL_SECRET or MINDEX_INTERNAL_TOKENS)"

    # Rate limiting check
    checks["checks"]["rate_limiting"] = "enabled" if settings.rate_limit_enabled else "disabled"

    return checks
