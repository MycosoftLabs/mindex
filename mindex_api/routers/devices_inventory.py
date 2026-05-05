"""devices.inventory + devices.deployment_suggestion — MINDEX device federation."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/devices-inventory", tags=["Devices Inventory"])


class InventoryUpsert(BaseModel):
    device_key: str = Field(..., min_length=1, max_length=256)
    device_type: str = Field(..., min_length=1, max_length=64)
    serial: Optional[str] = None
    status: str = Field(default="unknown", max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/inventory")
async def list_inventory(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status (e.g. deployed, production, active)"),
    db: AsyncSession = Depends(get_db_session),
):
    q = """
            SELECT id::text, device_key, device_type, serial, status, metadata, created_at, updated_at
            FROM devices.inventory
        """
    params: dict[str, Any] = {"lim": limit, "off": offset}
    if status:
        q += " WHERE lower(status) = lower(:st)"
        params["st"] = status
    q += " ORDER BY updated_at DESC LIMIT :lim OFFSET :off"
    r = await db.execute(text(q), params)
    return {"items": [dict(x) for x in r.mappings().all()]}


@router.get("/inventory/deployed/summary")
async def deployed_summary(db: AsyncSession = Depends(get_db_session)):
    """Counts by status for dashboard chips (real rows only)."""
    r = await db.execute(
        text(
            """
            SELECT status, COUNT(*)::int AS n
            FROM devices.inventory
            GROUP BY status
            ORDER BY n DESC
            """
        )
    )
    return {"items": [dict(x) for x in r.mappings().all()]}


@router.get("/inventory/{device_key:path}")
async def get_by_key(device_key: str, db: AsyncSession = Depends(get_db_session)):
    row = (
        await db.execute(
            text(
                """
                SELECT id::text, device_key, device_type, serial, status, metadata, created_at, updated_at
                FROM devices.inventory
                WHERE device_key = :k
                """
            ),
            {"k": device_key},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="device_not_found")
    return {"item": dict(row)}


@router.post("/inventory")
async def upsert_inventory(body: InventoryUpsert, db: AsyncSession = Depends(get_db_session)):
    await db.execute(
        text(
            """
            INSERT INTO devices.inventory (device_key, device_type, serial, status, metadata)
            VALUES (:device_key, :device_type, :serial, :status, CAST(:metadata AS jsonb))
            ON CONFLICT (device_key) DO UPDATE SET
                device_type = EXCLUDED.device_type,
                serial = COALESCE(EXCLUDED.serial, devices.inventory.serial),
                status = EXCLUDED.status,
                metadata = devices.inventory.metadata || EXCLUDED.metadata,
                updated_at = now()
            """
        ),
        {
            "device_key": body.device_key,
            "device_type": body.device_type,
            "serial": body.serial,
            "status": body.status,
            "metadata": json.dumps(body.metadata),
        },
    )
    await db.commit()
    return {"status": "ok", "device_key": body.device_key}


@router.get("/suggestions")
async def list_suggestions(
    status: Optional[str] = Query(None, description="Filter by status, e.g. open"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    q = """
        SELECT s.id::text, s.inventory_id::text, s.rationale, s.priority, s.status, s.created_at,
               i.device_key, i.device_type
        FROM devices.deployment_suggestion s
        LEFT JOIN devices.inventory i ON i.id = s.inventory_id
    """
    params: dict[str, Any] = {"lim": limit}
    if status:
        q += " WHERE s.status = :st"
        params["st"] = status
    q += " ORDER BY s.priority DESC, s.created_at DESC LIMIT :lim"
    r = await db.execute(text(q), params)
    return {"items": [dict(x) for x in r.mappings().all()]}


@router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(suggestion_id: str, db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(
        text(
            """
            UPDATE devices.deployment_suggestion
            SET status = 'approved', resolved_at = now()
            WHERE id = :id::uuid AND status = 'open'
            RETURNING id::text
            """
        ),
        {"id": suggestion_id},
    )
    row = res.mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="suggestion_not_found_or_not_open")
    return {"status": "approved", "id": row["id"]}


class InventoryStatusBody(BaseModel):
    status: str = Field(..., min_length=1, max_length=32)


@router.get("/inventory/deployed")
async def list_deployed(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
):
    """Rows commonly treated as in-field (status names vary by integrator)."""
    r = await db.execute(
        text(
            """
            SELECT id::text, device_key, device_type, serial, status, metadata, created_at, updated_at
            FROM devices.inventory
            WHERE lower(status) IN ('deployed', 'production', 'active', 'in_service', 'field')
            ORDER BY updated_at DESC
            LIMIT :lim OFFSET :off
            """
        ),
        {"lim": limit, "off": offset},
    )
    return {"items": [dict(x) for x in r.mappings().all()]}


class InventoryMoveBody(BaseModel):
    site_label: str = Field(..., min_length=1, max_length=256)
    note: Optional[str] = Field(None, max_length=1024)


@router.post("/inventory/{inventory_id}/move")
async def move_inventory(inventory_id: str, body: InventoryMoveBody, db: AsyncSession = Depends(get_db_session)):
    """Append move metadata (JSON merge) — no fabricated device rows."""
    note = body.note or ""
    patch = json.dumps({"last_move": {"site": body.site_label, "note": note}})
    res = await db.execute(
        text(
            """
            UPDATE devices.inventory
            SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:patch AS jsonb),
                updated_at = now()
            WHERE id = :id::uuid
            RETURNING id::text
            """
        ),
        {"patch": patch, "id": inventory_id},
    )
    row = res.mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="inventory_not_found")
    return {"status": "ok", "id": row["id"]}


class InventoryReplaceBody(BaseModel):
    replacement_device_key: str = Field(..., min_length=1, max_length=256)


@router.post("/inventory/{inventory_id}/replace")
async def replace_inventory(inventory_id: str, body: InventoryReplaceBody, db: AsyncSession = Depends(get_db_session)):
    """Record replacement intent on inventory metadata (operations team completes swap out-of-band)."""
    patch = json.dumps(
        {
            "replacement_device_key": body.replacement_device_key,
            "replacement_marked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    res = await db.execute(
        text(
            """
            UPDATE devices.inventory
            SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:patch AS jsonb),
                status = CASE WHEN lower(status) = 'production' THEN status ELSE 'pending_replace' END,
                updated_at = now()
            WHERE id = :id::uuid
            RETURNING id::text
            """
        ),
        {"patch": patch, "id": inventory_id},
    )
    row = res.mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="inventory_not_found")
    return {"status": "ok", "id": row["id"]}


@router.post("/inventory/{inventory_id}/status")
async def set_inventory_status(inventory_id: str, body: InventoryStatusBody, db: AsyncSession = Depends(get_db_session)):
    """Update deployment status (e.g. staged → deployed)."""
    res = await db.execute(
        text(
            """
            UPDATE devices.inventory
            SET status = :st, updated_at = now()
            WHERE id = :id::uuid
            RETURNING id::text
            """
        ),
        {"st": body.status, "id": inventory_id},
    )
    row = res.mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="inventory_not_found")
    return {"status": "ok", "id": row["id"]}


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(suggestion_id: str, db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(
        text(
            """
            UPDATE devices.deployment_suggestion
            SET status = 'rejected', resolved_at = now()
            WHERE id = :id::uuid AND status = 'open'
            RETURNING id::text
            """
        ),
        {"id": suggestion_id},
    )
    row = res.mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="suggestion_not_found_or_not_open")
    return {"status": "rejected", "id": row["id"]}
