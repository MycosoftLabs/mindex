from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import async_session_scope
from ..dependencies import get_db_session
from ..ledger.btc_ordinals_client import fetch_bitcoin_chain_tip, ordinals_readiness
from ..ledger.dag import fetch_dag_path, insert_dag_node
from ..ledger.platform_one_client import ping_platform_one
from ..ledger.solana_client import fetch_recent_prioritization_fees, fetch_solana_health

router = APIRouter(tags=["Ledger"])


@router.get("/ledger")
async def get_ledger_status():
    rpc = str(settings.solana_rpc_url) if settings.solana_rpc_url else ""
    sol = await fetch_solana_health(rpc) if rpc else {"connected": False, "health": "not_configured"}
    fee = await fetch_recent_prioritization_fees(rpc) if rpc else None
    if fee is not None:
        sol["estimated_fee_sol"] = fee
    btc = await fetch_bitcoin_chain_tip()
    p1 = await ping_platform_one()
    hg = {
        "connected": bool(settings.hypergraph_endpoint),
        "node_url": str(settings.hypergraph_endpoint) if settings.hypergraph_endpoint else None,
        "status": "configured" if settings.hypergraph_endpoint else "offline",
    }
    ord_r = await ordinals_readiness(settings.btc_ordinals_wallet)
    return {
        "hypergraph": hg,
        "solana": sol,
        "bitcoin": btc,
        "platform_one": p1,
        "ordinals": ord_r,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


async def _anchor_sse() -> AsyncIterator[bytes]:
    """Poll latest anchors — yields SSE frames (own DB session per tick)."""
    last_id: Optional[str] = None
    while True:
        try:
            async with async_session_scope() as session:
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT id::text, encode(content_hash, 'hex') AS ch, entity_type, tier, created_at
                            FROM ledger.anchor
                            ORDER BY created_at DESC
                            LIMIT 1
                            """
                        )
                    )
                ).mappings().first()
            payload: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "latest": None}
            if row:
                rid = row["id"]
                if rid != last_id:
                    last_id = rid
                    payload["latest"] = dict(row)
            yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)[:200]})}\n\n".encode("utf-8")
        await asyncio.sleep(2)


@router.get("/ledger/stream")
async def ledger_stream():
    return StreamingResponse(_anchor_sse(), media_type="text/event-stream")


@router.get("/ledger/anchors")
async def list_anchors(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        text(
            """
            SELECT id, entity_type, entity_id, encode(content_hash, 'hex') AS content_hash_hex,
                   tier, solana_signature, ordinal_inscription_id, platform_one_ref, created_at
            FROM ledger.anchor
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    )
    rows = [dict(r) for r in result.mappings().all()]
    return {"items": rows, "limit": limit, "offset": offset}


@router.get("/ledger/anchors/by-entity/{entity_type}/{entity_id}")
async def anchors_for_entity(entity_type: str, entity_id: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT id, entity_type, entity_id, encode(content_hash, 'hex') AS content_hash_hex,
                   tier, solana_signature, ordinal_inscription_id, platform_one_ref, created_at
            FROM ledger.anchor
            WHERE entity_type = :t AND entity_id = :id::uuid
            ORDER BY created_at DESC
            """
        ),
        {"t": entity_type, "id": entity_id},
    )
    return {"items": [dict(r) for r in result.mappings().all()]}


@router.get("/ledger/dag/epoch/current")
async def dag_epoch_current(db: AsyncSession = Depends(get_db_session)):
    row = (
        await db.execute(text("SELECT COALESCE(MAX(epoch), 0)::bigint AS epoch FROM ledger.dag_node"))
    ).mappings().first()
    return {"epoch": int(row["epoch"]) if row else 0}


@router.get("/ledger/dag/path/{content_hash}")
async def dag_path(content_hash: str, db: AsyncSession = Depends(get_db_session)):
    items = await fetch_dag_path(db, content_hash)
    return {"content_hash": content_hash, "nodes": items}


class AnchorBody(BaseModel):
    entity_type: str = Field(..., min_length=1, max_length=64)
    entity_id: str
    content_hash_hex: str = Field(..., min_length=64, max_length=66)
    tier: str = "dag"


@router.post("/ledger/anchor")
async def post_anchor(body: AnchorBody, db: AsyncSession = Depends(get_db_session)):
    """Register anchor row + DAG node (internal ops; protect at edge with API key)."""
    h = body.content_hash_hex.lower().lstrip("0x")
    if len(h) != 64:
        raise HTTPException(status_code=400, detail="content_hash_hex must be 64 hex chars")
    raw = bytes.fromhex(h)
    dag = await insert_dag_node(db, content_hash=raw, epoch=0, metadata={"entity_type": body.entity_type})
    nid = dag["id"]
    await db.execute(
        text(
            """
            INSERT INTO ledger.anchor (entity_type, entity_id, content_hash, tier, hypergraph_node_id, metadata)
            VALUES (:et, :eid::uuid, :ch, :tier, :nid::uuid, '{}'::jsonb)
            """
        ),
        {
            "et": body.entity_type,
            "eid": body.entity_id,
            "ch": raw,
            "tier": body.tier,
            "nid": nid,
        },
    )
    await db.commit()
    return {"status": "recorded", "dag_node_id": str(nid)}


@router.post("/ledger/mark-ip/{entity_id}")
async def mark_ip(entity_id: str, db: AsyncSession = Depends(get_db_session)):
    """Flag taxon for IP review tier when content_hash exists (requires migration 0031)."""
    res = await db.execute(
        text(
            """
            INSERT INTO ledger.anchor (entity_type, entity_id, content_hash, tier, metadata)
            SELECT 'taxon', id, content_hash, 'ip_review', jsonb_build_object('marked', true, 'reason', 'user_or_agent_flag')
            FROM core.taxon
            WHERE id = :id::uuid AND content_hash IS NOT NULL
            RETURNING id
            """
        ),
        {"id": entity_id},
    )
    row = res.first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="taxon not found or content_hash not backfilled yet")
    return {"status": "queued", "entity_id": entity_id, "anchor_id": str(row[0])}


@router.get("/ledger/wallet/balances")
async def wallet_balances():
    """Non-custodial: returns configuration presence only (balances require wallet RPC integration)."""
    return {
        "solana_keypair_configured": bool(settings.solana_keypair_path),
        "btc_ordinals_wallet_configured": bool(settings.btc_ordinals_wallet),
        "p1_configured": bool(settings.p1_api_key and settings.p1_base_url),
    }
