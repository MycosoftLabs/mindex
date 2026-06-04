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
from ..ledger.anchor_service import anchor_record_on_ledger
from ..ledger.bitcoin_rpc_client import fetch_bitcoin_rpc_status
from ..ledger.btc_ordinals_client import fetch_bitcoin_chain_tip, ordinals_readiness
from ..ledger.dag import fetch_dag_path, insert_dag_node
from ..ledger.hypergraph_client import fetch_hypergraph_status
from ..ledger.platform_one_client import ping_platform_one
from ..ledger.solana_client import (
    fetch_recent_prioritization_fees,
    fetch_spl_mint_summary,
    resolve_working_solana_rpc,
)

router = APIRouter(tags=["Ledger"])


async def _safe_dag_stats(db: AsyncSession) -> dict[str, int]:
    """DAG counts from ledger.dag_node; empty stats if schema grants are missing."""
    try:
        row = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)::bigint AS nodes,
                           COALESCE(MAX(epoch), 0)::bigint AS epoch
                    FROM ledger.dag_node
                    """
                )
            )
        ).mappings().first()
        if row:
            return {
                "nodes": int(row["nodes"] or 0),
                "epoch": int(row["epoch"] or 0),
            }
    except Exception:
        await db.rollback()
    return {"nodes": 0, "epoch": 0}


@router.get("/ledger")
async def get_ledger_status(db: AsyncSession = Depends(get_db_session)):
    rpc, sol = await resolve_working_solana_rpc(settings.solana_rpc_candidates())
    if not rpc:
        sol.setdefault("health", "not_configured")
        sol["rpc_url"] = (
            str(settings.solana_rpc_url) if settings.solana_rpc_url else None
        )
    fee = await fetch_recent_prioritization_fees(rpc) if rpc else None
    if fee is not None:
        sol["estimated_fee_sol"] = fee
    sol["network"] = settings.solana_network
    mint = (settings.myca_solana_mint or "").strip()
    sol["myca_token"] = await fetch_spl_mint_summary(rpc, mint) if mint and rpc else {
        "configured": bool(mint),
        "mint": mint or None,
        "connected": False,
    }
    sol["keypair_configured"] = bool(settings.solana_keypair_path)

    btc_rpc = str(settings.bitcoin_rpc_url) if settings.bitcoin_rpc_url else ""
    btc_local = (
        await fetch_bitcoin_rpc_status(
            btc_rpc,
            settings.bitcoin_rpc_user,
            settings.bitcoin_rpc_password,
        )
        if btc_rpc
        else {"connected": False, "source": "bitcoin_core_rpc"}
    )
    btc_public = await fetch_bitcoin_chain_tip()
    btc = {
        **btc_public,
        "local_node": btc_local,
        "ordinals_wallet_configured": bool(settings.btc_ordinals_wallet),
        "anchor_modes": ["ordinals_inscription", "op_return"],
    }
    if btc_local.get("connected"):
        btc["connected"] = True
        btc["block_height"] = btc_local.get("block_height") or btc_public.get("block_height")

    p1 = await ping_platform_one()
    hg = await fetch_hypergraph_status()
    dag_stats = await _safe_dag_stats(db)
    hg["dag_nodes"] = dag_stats["nodes"]
    hg["dag_height"] = dag_stats["epoch"]
    ord_r = await ordinals_readiness(settings.btc_ordinals_wallet)
    return {
        "hypergraph": hg,
        "solana": sol,
        "bitcoin": btc,
        "platform_one": p1,
        "ordinals": ord_r,
        "infrastructure": {
            "required_nodes": [
                {
                    "id": "bitcoin",
                    "label": "Bitcoin Core (optional local) + mempool.space read",
                    "env": ["BITCOIN_RPC_URL", "BTC_ORDINALS_WALLET"],
                },
                {
                    "id": "solana",
                    "label": "Solana RPC + validator (MYCA SPL mint)",
                    "env": ["SOLANA_RPC_URL", "MYCA_SOLANA_MINT", "SOLANA_KEYPAIR_PATH"],
                },
                {
                    "id": "hypergraph",
                    "label": "Hypergraph decentralized DAG node",
                    "env": ["HYPERGRAPH_ENDPOINT"],
                },
                {
                    "id": "platform_one",
                    "label": "Platform One (defense correlation)",
                    "env": ["P1_BASE_URL", "P1_API_KEY"],
                },
            ],
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


class AnchorRecordsBody(BaseModel):
    record_ids: list[str] = Field(..., min_length=1, max_length=50)
    ledger: str = Field(..., pattern="^(hypergraph|solana|bitcoin)$")


@router.post("/ledger/anchor/records")
async def anchor_records(body: AnchorRecordsBody, db: AsyncSession = Depends(get_db_session)):
    """Anchor existing ledger.anchor rows to Hypergraph DAG, Solana (MYCA), or Bitcoin (OP_RETURN / ordinals)."""
    results = []
    for rid in body.record_ids:
        results.append(
            await anchor_record_on_ledger(db, record_id=rid.strip(), ledger=body.ledger)  # type: ignore[arg-type]
        )
    ok = all(r.get("ok") for r in results)
    first_tx = next((r.get("tx_id") for r in results if r.get("ok") and r.get("tx_id")), None)
    return {
        "ok": ok,
        "ledger": body.ledger,
        "tx_id": first_tx,
        "results": results,
        "message": "completed" if ok else "partial_or_failed",
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
    """Configuration + MYCA mint supply when Solana RPC is reachable."""
    rpc = str(settings.solana_rpc_url) if settings.solana_rpc_url else ""
    mint = (settings.myca_solana_mint or "").strip()
    token = await fetch_spl_mint_summary(rpc, mint) if rpc and mint else None
    return {
        "solana_keypair_configured": bool(settings.solana_keypair_path),
        "btc_ordinals_wallet_configured": bool(settings.btc_ordinals_wallet),
        "bitcoin_rpc_configured": bool(settings.bitcoin_rpc_url),
        "p1_configured": bool(settings.p1_api_key and settings.p1_base_url),
        "hypergraph_configured": bool(settings.hypergraph_endpoint),
        "myca_solana_mint": mint or None,
        "myca_token": token,
    }


@router.get("/ledger/infrastructure")
async def ledger_infrastructure():
    """Operator checklist: nodes to run on MINDEX host or adjacent hardware (e.g. Pi + 2TB for Bitcoin)."""
    return {
        "nodes": [
            {
                "service": "bitcoin",
                "description": "Bitcoin Core for RPC, OP_RETURN broadcast, Ordinals wallet",
                "recommended": "2TB+ SSD on dedicated host (Raspberry Pi or miner-adjacent machine)",
                "env": ["BITCOIN_RPC_URL", "BITCOIN_RPC_USER", "BITCOIN_RPC_PASSWORD", "BTC_ORDINALS_WALLET"],
            },
            {
                "service": "solana",
                "description": "Solana validator or trusted RPC; MYCA token mint from MycoDAO",
                "env": ["SOLANA_RPC_URL", "MYCA_SOLANA_MINT", "SOLANA_KEYPAIR_PATH"],
            },
            {
                "service": "hypergraph",
                "description": "Hypergraph decentralized DAG for hash anchors (correlates with Platform One)",
                "env": ["HYPERGRAPH_ENDPOINT"],
            },
            {
                "service": "platform_one",
                "description": "U.S. military Platform One API for defense-grade metadata correlation",
                "env": ["P1_BASE_URL", "P1_API_KEY"],
            },
        ],
        "data_plane": "MINDEX VM 189 persists ledger.anchor, ledger.dag_node, hypergraph_anchor, bitcoin_ordinal, solana_binding",
    }
