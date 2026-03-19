"""
API Key Management Router — CRUD, rotation, usage, and audit for API keys.

Internal-only (requires service token). Used by MAS admin, website dashboard.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/keys", tags=["API Key Management"])


class KeyInfo(BaseModel):
    id: str
    key_prefix: str
    name: str
    service: str
    scopes: list
    rate_limit_per_minute: int
    rate_limit_per_day: int
    usage_count: int
    is_active: bool
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: str


class KeyRotateRequest(BaseModel):
    key_id: str
    reason: Optional[str] = None


class KeyRotateResponse(BaseModel):
    new_api_key: str
    new_key_prefix: str
    old_key_id: str
    message: str = "Old key deactivated. Save new key — it will not be shown again."


class KeyUsageResponse(BaseModel):
    key_id: str
    windows: List[Dict[str, Any]]
    total_requests: int


class KeyAuditResponse(BaseModel):
    key_id: str
    entries: List[Dict[str, Any]]
    total: int


@router.get("", response_model=List[KeyInfo])
async def list_keys(
    user_id: Optional[str] = Query(None, description="Filter by user_id"),
    service: str = Query("worldview", description="Filter by service"),
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> List[KeyInfo]:
    """List API keys, optionally filtered by user and service."""
    conditions = ["service = :service"]
    params: dict = {"service": service, "limit": limit}

    if user_id:
        conditions.append("user_id = :user_id::uuid")
        params["user_id"] = user_id
    if active_only:
        conditions.append("is_active = true")

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT id, key_prefix, name, service, scopes, rate_limit_per_minute,
               rate_limit_per_day, usage_count, is_active, last_used_at,
               expires_at, created_at
        FROM api_keys
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit
    """)

    result = await db.execute(stmt, params)
    return [
        KeyInfo(
            id=str(row[0]),
            key_prefix=row[1],
            name=row[2],
            service=row[3],
            scopes=row[4] if isinstance(row[4], list) else [],
            rate_limit_per_minute=row[5],
            rate_limit_per_day=row[6],
            usage_count=row[7],
            is_active=row[8],
            last_used_at=row[9].isoformat() if row[9] else None,
            expires_at=row[10].isoformat() if row[10] else None,
            created_at=row[11].isoformat() if row[11] else "",
        )
        for row in result.fetchall()
    ]


@router.post("/rotate", response_model=KeyRotateResponse)
async def rotate_key(
    body: KeyRotateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> KeyRotateResponse:
    """Rotate an API key: generate new, deactivate old, link via rotated_from."""
    now = datetime.now(timezone.utc)

    # Get old key details
    old_stmt = text("""
        SELECT id, user_id, name, service, scopes, rate_limit_per_minute,
               rate_limit_per_day
        FROM api_keys
        WHERE id = :key_id::uuid AND is_active = true
    """)
    old_result = await db.execute(old_stmt, {"key_id": body.key_id})
    old_row = old_result.fetchone()

    if not old_row:
        raise HTTPException(status_code=404, detail="Active key not found")

    old_id, user_id, name, service, scopes, rate_min, rate_day = old_row

    # Generate new key
    raw = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]

    try:
        # Deactivate old key
        await db.execute(
            text("UPDATE api_keys SET is_active = false, updated_at = :now WHERE id = :key_id::uuid"),
            {"key_id": body.key_id, "now": now},
        )

        # Insert new key linked to old
        insert_stmt = text("""
            INSERT INTO api_keys (
                key_hash, key_prefix, name, service, scopes,
                rate_limit_per_minute, rate_limit_per_day,
                user_id, rotated_from, is_active, created_at, updated_at
            )
            VALUES (
                :key_hash, :key_prefix, :name, :service, :scopes::jsonb,
                :rate_min, :rate_day,
                :user_id, :rotated_from::uuid, true, :now, :now
            )
        """)
        await db.execute(insert_stmt, {
            "key_hash": key_hash,
            "key_prefix": prefix,
            "name": name,
            "service": service,
            "scopes": json.dumps(scopes if isinstance(scopes, list) else []),
            "rate_min": rate_min,
            "rate_day": rate_day,
            "user_id": str(user_id) if user_id else None,
            "rotated_from": body.key_id,
            "now": now,
        })

        # Audit log
        await db.execute(
            text("""
                INSERT INTO api_key_audit (key_id, action, metadata, created_at)
                VALUES (:key_id::uuid, 'rotate', :meta::jsonb, :now)
            """),
            {
                "key_id": body.key_id,
                "meta": json.dumps({"reason": body.reason, "new_key_prefix": prefix}),
                "now": now,
            },
        )

        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return KeyRotateResponse(
        new_api_key=raw,
        new_key_prefix=prefix,
        old_key_id=body.key_id,
    )


@router.delete("/{key_id}")
async def deactivate_key(
    key_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Deactivate an API key."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        text("UPDATE api_keys SET is_active = false, updated_at = :now WHERE id = :key_id::uuid RETURNING id"),
        {"key_id": key_id, "now": now},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Key not found")

    await db.execute(
        text("INSERT INTO api_key_audit (key_id, action, created_at) VALUES (:key_id::uuid, 'deactivate', :now)"),
        {"key_id": key_id, "now": now},
    )
    await db.commit()
    return {"status": "deactivated", "key_id": key_id}


@router.get("/{key_id}/usage", response_model=KeyUsageResponse)
async def key_usage(
    key_id: str,
    window_type: str = Query("minute", description="Window type: minute or day"),
    limit: int = Query(60, ge=1, le=1440),
    db: AsyncSession = Depends(get_db_session),
) -> KeyUsageResponse:
    """Get usage stats for an API key."""
    stmt = text("""
        SELECT window_start, window_type, request_count
        FROM api_key_usage
        WHERE key_id = :key_id::uuid AND window_type = :window_type
        ORDER BY window_start DESC
        LIMIT :limit
    """)
    result = await db.execute(stmt, {"key_id": key_id, "window_type": window_type, "limit": limit})
    windows = [
        {"window_start": row[0].isoformat(), "window_type": row[1], "request_count": row[2]}
        for row in result.fetchall()
    ]

    total_stmt = text("SELECT usage_count FROM api_keys WHERE id = :key_id::uuid")
    total = (await db.execute(total_stmt, {"key_id": key_id})).scalar() or 0

    return KeyUsageResponse(key_id=key_id, windows=windows, total_requests=total)


@router.get("/{key_id}/audit", response_model=KeyAuditResponse)
async def key_audit(
    key_id: str,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
) -> KeyAuditResponse:
    """Get audit log for an API key."""
    stmt = text("""
        SELECT action, ip_address, user_agent, endpoint, metadata, created_at
        FROM api_key_audit
        WHERE key_id = :key_id::uuid
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    result = await db.execute(stmt, {"key_id": key_id, "limit": limit})
    entries = [
        {
            "action": row[0],
            "ip_address": str(row[1]) if row[1] else None,
            "user_agent": row[2],
            "endpoint": row[3],
            "metadata": row[4] or {},
            "created_at": row[5].isoformat() if row[5] else None,
        }
        for row in result.fetchall()
    ]

    return KeyAuditResponse(key_id=key_id, entries=entries, total=len(entries))
