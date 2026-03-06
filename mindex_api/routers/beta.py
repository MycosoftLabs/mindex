"""
Beta Users Router - March 5, 2026
Tracks beta signups for revenue validation (MYCA Loop Closure Plan).
Creates beta users, generates API keys, stores in beta_users table.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/beta", tags=["beta"])


class OnboardRequest(BaseModel):
    """Request body for beta user onboarding."""
    email: EmailStr
    plan: str = "free"
    supabase_user_id: Optional[str] = None


class OnboardResponse(BaseModel):
    """Response with API key (shown only once)."""
    api_key: str
    api_key_prefix: str
    message: str = "Save your API key. It will not be shown again."


def _generate_api_key() -> tuple[str, str, str]:
    """Generate secure API key and return (raw_key, hash, prefix)."""
    raw = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12] if len(raw) >= 12 else raw
    return raw, key_hash, prefix


@router.post("/onboard", response_model=OnboardResponse)
async def onboard_beta_user(
    body: OnboardRequest,
    db: AsyncSession = Depends(get_db_session),
) -> OnboardResponse:
    """
    Onboard a beta user: create or update record, generate API key.
    The raw API key is returned only on first onboard; subsequent calls
    for same email return a new key and update the record.
    """
    raw_key, key_hash, prefix = _generate_api_key()
    now = datetime.now(timezone.utc).isoformat()

    stmt = text("""
        INSERT INTO beta_users (
            email, plan, api_key_hash, api_key_prefix,
            supabase_user_id, signup_date, created_at, updated_at
        )
        VALUES (
            :email, :plan, :api_key_hash, :api_key_prefix,
            :supabase_user_id, :now, :now, :now
        )
        ON CONFLICT (email) DO UPDATE SET
            plan = EXCLUDED.plan,
            api_key_hash = EXCLUDED.api_key_hash,
            api_key_prefix = EXCLUDED.api_key_prefix,
            supabase_user_id = COALESCE(EXCLUDED.supabase_user_id, beta_users.supabase_user_id),
            updated_at = EXCLUDED.updated_at
    """)
    try:
        await db.execute(stmt, {
            "email": body.email,
            "plan": body.plan.lower(),
            "api_key_hash": key_hash,
            "api_key_prefix": prefix,
            "supabase_user_id": body.supabase_user_id,
            "now": now,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to onboard: {str(e)}",
        ) from e

    return OnboardResponse(
        api_key=raw_key,
        api_key_prefix=prefix,
        message="Save your API key securely. It will not be shown again.",
    )


class BetaStatsResponse(BaseModel):
    """Beta user and API usage stats for MRR dashboard."""

    active_users: int = 0
    total_api_calls: int = 0
    users_by_plan: dict[str, int] = {}


@router.get("/stats", response_model=BetaStatsResponse)
async def get_beta_stats(
    db: AsyncSession = Depends(get_db_session),
) -> BetaStatsResponse:
    """
    Return beta user counts and total API usage for MRR dashboard.
    Called by website /api/billing/mrr (super admin only).
    """
    try:
        count_stmt = text("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(usage_count), 0)::INTEGER AS total_usage
            FROM beta_users
        """)
        plan_stmt = text("""
            SELECT plan, COUNT(*) AS cnt FROM beta_users GROUP BY plan
        """)
        count_row = (await db.execute(count_stmt)).fetchone()
        plan_rows = (await db.execute(plan_stmt)).fetchall()
    except Exception:
        return BetaStatsResponse()

    active_users = count_row[0] if count_row else 0
    total_api_calls = count_row[1] if count_row and len(count_row) > 1 else 0
    users_by_plan = {r[0]: r[1] for r in plan_rows} if plan_rows else {}
    return BetaStatsResponse(
        active_users=active_users,
        total_api_calls=total_api_calls,
        users_by_plan=users_by_plan,
    )
