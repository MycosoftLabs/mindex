"""
Beta Users Router - March 5, 2026 (Updated March 19, 2026)
Tracks beta signups for revenue validation (MYCA Loop Closure Plan).
Creates beta users, generates API keys, stores in both beta_users AND
api_keys/user_registry tables for proper Worldview API auth.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/beta", tags=["beta"])


class OnboardRequest(BaseModel):
    """Request body for beta user onboarding."""
    email: EmailStr
    plan: str = "free"
    user_type: str = "human"  # 'human' or 'agent'
    supabase_user_id: Optional[str] = None
    startup_fee_paid: bool = False
    agent_metadata: Optional[dict] = None


class OnboardResponse(BaseModel):
    """Response with API key (shown only once)."""
    api_key: str
    api_key_prefix: str
    plan: str
    user_type: str
    rate_limit_per_minute: int
    rate_limit_per_day: int
    message: str = "Save your API key. It will not be shown again."


def _generate_api_key() -> tuple[str, str, str]:
    """Generate secure API key and return (raw_key, hash, prefix)."""
    raw = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12] if len(raw) >= 12 else raw
    return raw, key_hash, prefix


def _get_rate_limits(plan: str) -> tuple[int, int]:
    """Get rate limits for a plan tier."""
    limits = settings.worldview_rate_limits.get(plan, settings.worldview_rate_limits.get("free", {}))
    return limits.get("per_minute", 10), limits.get("per_day", 1000)


@router.post("/onboard", response_model=OnboardResponse)
async def onboard_beta_user(
    body: OnboardRequest,
    db: AsyncSession = Depends(get_db_session),
) -> OnboardResponse:
    """
    Onboard a beta user: create user_registry + api_keys + beta_users records.

    The raw API key is returned only once. Subsequent calls for the same email
    generate a new key (old key is deactivated).

    Flow:
    1. Upsert user_registry row (unified identity)
    2. Deactivate any existing api_keys for this user
    3. Insert new api_keys row with worldview scope and plan-appropriate rate limits
    4. Upsert beta_users for backward compatibility
    5. Return raw API key (shown only once)
    """
    raw_key, key_hash, prefix = _generate_api_key()
    now = datetime.now(timezone.utc)
    plan = body.plan.lower()
    user_type = body.user_type.lower() if body.user_type in ("human", "agent") else "human"
    rate_per_min, rate_per_day = _get_rate_limits(plan)

    try:
        # 1. Upsert user_registry
        user_stmt = text("""
            INSERT INTO user_registry (
                email, display_name, user_type, plan_tier,
                payment_status, startup_fee_paid, agent_metadata,
                created_at, updated_at
            )
            VALUES (
                :email, :email, :user_type, :plan,
                :payment_status, :startup_fee_paid, :agent_metadata::jsonb,
                :now, :now
            )
            ON CONFLICT (email) DO UPDATE SET
                user_type = EXCLUDED.user_type,
                plan_tier = EXCLUDED.plan_tier,
                startup_fee_paid = GREATEST(user_registry.startup_fee_paid, EXCLUDED.startup_fee_paid),
                agent_metadata = COALESCE(EXCLUDED.agent_metadata, user_registry.agent_metadata),
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """)
        user_result = await db.execute(user_stmt, {
            "email": body.email,
            "user_type": user_type,
            "plan": plan,
            "payment_status": "active" if body.startup_fee_paid else "pending",
            "startup_fee_paid": body.startup_fee_paid,
            "agent_metadata": json.dumps(body.agent_metadata or {}),
            "now": now,
        })
        user_row = user_result.fetchone()
        user_id = str(user_row[0]) if user_row else None

        # 2. Deactivate existing api_keys for this user
        if user_id:
            await db.execute(
                text("UPDATE api_keys SET is_active = false, updated_at = :now WHERE user_id = :user_id::uuid AND service = 'worldview'"),
                {"user_id": user_id, "now": now},
            )

        # 3. Insert new api_keys row
        api_key_stmt = text("""
            INSERT INTO api_keys (
                key_hash, key_prefix, name, service, scopes,
                rate_limit_per_minute, rate_limit_per_day,
                user_id, is_active, created_at, updated_at
            )
            VALUES (
                :key_hash, :key_prefix, :name, 'worldview', :scopes::jsonb,
                :rate_per_min, :rate_per_day,
                :user_id::uuid, true, :now, :now
            )
        """)
        await db.execute(api_key_stmt, {
            "key_hash": key_hash,
            "key_prefix": prefix,
            "name": f"{body.email} ({plan})",
            "scopes": '["worldview:read"]',
            "rate_per_min": rate_per_min,
            "rate_per_day": rate_per_day,
            "user_id": user_id,
            "now": now,
        })

        # 4. Upsert beta_users for backward compatibility
        beta_stmt = text("""
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
        await db.execute(beta_stmt, {
            "email": body.email,
            "plan": plan,
            "api_key_hash": key_hash,
            "api_key_prefix": prefix,
            "supabase_user_id": body.supabase_user_id,
            "now": now.isoformat(),
        })

        await db.commit()
        logger.info(f"Onboarded user: {body.email} (plan={plan}, type={user_type})")
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to onboard: {str(e)}",
        ) from e

    return OnboardResponse(
        api_key=raw_key,
        api_key_prefix=prefix,
        plan=plan,
        user_type=user_type,
        rate_limit_per_minute=rate_per_min,
        rate_limit_per_day=rate_per_day,
        message="Save your API key securely. It will not be shown again.",
    )


class BetaStatsResponse(BaseModel):
    """Beta user and API usage stats for MRR dashboard."""

    active_users: int = 0
    total_api_calls: int = 0
    users_by_plan: dict[str, int] = {}
    users_by_type: dict[str, int] = {}
    active_api_keys: int = 0
    startup_fees_collected: int = 0


@router.get("/stats", response_model=BetaStatsResponse)
async def get_beta_stats(
    db: AsyncSession = Depends(get_db_session),
) -> BetaStatsResponse:
    """
    Return beta user counts and total API usage for MRR dashboard.
    Called by website /api/billing/mrr (super admin only).
    """
    try:
        # Beta users stats
        count_stmt = text("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(usage_count), 0)::INTEGER AS total_usage
            FROM beta_users
        """)
        plan_stmt = text("""
            SELECT plan, COUNT(*) AS cnt FROM beta_users GROUP BY plan
        """)
        count_row = (await db.execute(count_stmt)).fetchone()
        plan_rows = (await db.execute(plan_stmt)).fetchall()

        # User registry stats (if available)
        try:
            type_stmt = text("SELECT user_type, COUNT(*) FROM user_registry GROUP BY user_type")
            type_rows = (await db.execute(type_stmt)).fetchall()
            users_by_type = {r[0]: r[1] for r in type_rows} if type_rows else {}
        except Exception:
            users_by_type = {}

        try:
            key_stmt = text("SELECT COUNT(*) FROM api_keys WHERE is_active = true AND service = 'worldview'")
            key_count = (await db.execute(key_stmt)).scalar() or 0
        except Exception:
            key_count = 0

        try:
            fee_stmt = text("SELECT COUNT(*) FROM user_registry WHERE startup_fee_paid = true")
            fees = (await db.execute(fee_stmt)).scalar() or 0
        except Exception:
            fees = 0

    except Exception:
        return BetaStatsResponse()

    active_users = count_row[0] if count_row else 0
    total_api_calls = count_row[1] if count_row and len(count_row) > 1 else 0
    users_by_plan = {r[0]: r[1] for r in plan_rows} if plan_rows else {}
    return BetaStatsResponse(
        active_users=active_users,
        total_api_calls=total_api_calls,
        users_by_plan=users_by_plan,
        users_by_type=users_by_type,
        active_api_keys=key_count,
        startup_fees_collected=fees,
    )
