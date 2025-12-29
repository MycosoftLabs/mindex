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
    return HealthResponse(status="ok", db=db_state, timestamp=datetime.now(timezone.utc), service="mindex", version=settings.api_version, git_sha=_get_git_sha())


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Return service version information."""
    return VersionResponse(
        service="mindex",
        version=settings.api_version,
        git_sha=_get_git_sha(),
    )
