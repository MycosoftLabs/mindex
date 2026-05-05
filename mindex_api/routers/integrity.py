"""Integrity / anchoring views for MINDEX entities (ledger.anchor + content_hash presence)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import async_session_scope
from ..dependencies import get_db_session

router = APIRouter(prefix="/integrity", tags=["Integrity"])
verify_router = APIRouter(prefix="/verify", tags=["Integrity"])


async def _safe_count(db: AsyncSession, sql: str) -> int | None:
    try:
        return (await db.execute(text(sql))).scalar()
    except Exception:
        return None


@router.get("/summary")
async def integrity_summary(db: AsyncSession = Depends(get_db_session)):
    tax = await _safe_count(db, "SELECT COUNT(*)::int FROM core.taxon")
    tax_h = await _safe_count(db, "SELECT COUNT(*)::int FROM core.taxon WHERE content_hash IS NOT NULL")
    gen = await _safe_count(db, "SELECT COUNT(*)::int FROM bio.genome")
    gen_h = await _safe_count(db, "SELECT COUNT(*)::int FROM bio.genome WHERE content_hash IS NOT NULL")
    tc = await _safe_count(db, "SELECT COUNT(*)::int FROM bio.taxon_compound")
    tc_h = await _safe_count(db, "SELECT COUNT(*)::int FROM bio.taxon_compound WHERE content_hash IS NOT NULL")
    anch = await _safe_count(db, "SELECT COUNT(*)::int FROM ledger.anchor")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_hashes": {
            "taxon": {"total": tax, "hashed": tax_h},
            "genome": {"total": gen, "hashed": gen_h},
            "taxon_compound": {"total": tc, "hashed": tc_h},
        },
        "anchors_total": anch,
    }


@router.get("/anchors/recent")
async def recent_anchors(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    r = await db.execute(
        text(
            """
            SELECT id::text, entity_type, entity_id::text, encode(content_hash, 'hex') AS content_hash_hex,
                   tier, solana_signature, ordinal_inscription_id, platform_one_ref, created_at
            FROM ledger.anchor
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"lim": limit},
    )
    return {"items": [dict(x) for x in r.mappings().all()]}


@router.get("/entity/{entity_type}/{entity_id}")
async def integrity_for_entity(entity_type: str, entity_id: str, db: AsyncSession = Depends(get_db_session)):
    r = await db.execute(
        text(
            """
            SELECT id::text, entity_type, entity_id::text, encode(content_hash, 'hex') AS content_hash_hex,
                   tier, solana_signature, ordinal_inscription_id, platform_one_ref, created_at, metadata
            FROM ledger.anchor
            WHERE entity_type = :et AND entity_id = :eid::uuid
            ORDER BY created_at DESC
            """
        ),
        {"et": entity_type, "eid": entity_id},
    )
    rows = [dict(x) for x in r.mappings().all()]
    ch = None
    if entity_type == "taxon":
        t = (
            await db.execute(
                text("SELECT encode(content_hash, 'hex') AS h FROM core.taxon WHERE id = :id::uuid"),
                {"id": entity_id},
            )
        ).mappings().first()
        ch = t["h"] if t else None
    return {"entity_type": entity_type, "entity_id": entity_id, "current_content_hash_hex": ch, "anchors": rows}


async def _integrity_stream() -> AsyncIterator[bytes]:
    while True:
        try:
            async with async_session_scope() as session:
                c = (await session.execute(text("SELECT COUNT(*)::int FROM ledger.anchor"))).scalar() or 0
                last = (
                    await session.execute(
                        text(
                            """
                            SELECT id::text, encode(content_hash, 'hex') AS content_hash_hex, tier, created_at
                            FROM ledger.anchor
                            ORDER BY created_at DESC
                            LIMIT 1
                            """
                        )
                    )
                ).mappings().first()
            payload: dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "anchor_count": c,
                "latest_anchor": dict(last) if last else None,
            }
            yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)[:200]})}\n\n".encode("utf-8")
        await asyncio.sleep(3)


@router.get("/stream")
async def integrity_stream():
    return StreamingResponse(_integrity_stream(), media_type="text/event-stream")


def _metadata_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


@router.get("/record/{record_id}")
async def integrity_record_for_chain(record_id: str, db: AsyncSession = Depends(get_db_session)):
    """Map ledger.anchor row to website hash-chain MINDEXRecord (or embedded mindex_record in metadata)."""
    r = await db.execute(
        text(
            """
            SELECT id::text, entity_type, entity_id::text, encode(content_hash, 'hex') AS ch_hex,
                   metadata, created_at
            FROM ledger.anchor
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": record_id},
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="anchor_not_found")
    meta = _metadata_dict(row.get("metadata"))
    embedded = meta.get("mindex_record")
    if isinstance(embedded, dict) and embedded.get("record_id"):
        return embedded

    ch_hex = str(row["ch_hex"] or "")
    data_hash = f"sha256:{ch_hex}" if ch_hex else "sha256:"
    prev_raw = meta.get("prev_hash")
    prev_hash = None
    if prev_raw is not None and str(prev_raw).strip():
        ps = str(prev_raw).strip()
        prev_hash = ps if ps.startswith("sha256:") else (f"sha256:{ps}" if len(ps) == 64 else ps)

    sig = str(meta.get("signature") or "")
    if not sig.startswith("ed25519:"):
        sig = f"ed25519:{sig}" if sig else "ed25519:"

    payload = meta.get("payload")
    if not isinstance(payload, dict):
        payload = {
            "entity_type": row.get("entity_type"),
            "entity_id": row.get("entity_id"),
            "source": "ledger.anchor",
        }

    ts = row.get("created_at")
    ts_out = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    out: dict[str, Any] = {
        "record_id": row["id"],
        "data_hash": data_hash,
        "prev_hash": prev_hash,
        "signature": sig,
        "timestamp": ts_out,
        "payload": payload,
    }
    pk = meta.get("public_key")
    if isinstance(pk, str) and pk.strip():
        out["public_key"] = pk.strip()
    return out


@router.get("/proof/{record_id}")
async def integrity_proof(record_id: str, db: AsyncSession = Depends(get_db_session)):
    """Merkle placeholder: single-leaf proof from anchor content_hash (full batch roots use DAG ingest)."""
    r = await db.execute(
        text(
            """
            SELECT encode(content_hash, 'hex') AS leaf_hex, created_at
            FROM ledger.anchor
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": record_id},
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="anchor_not_found")
    leaf_hex = str(row["leaf_hex"] or "")
    leaf = f"sha256:{leaf_hex}" if leaf_hex else "sha256:"
    ts = row.get("created_at")
    date_out = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return {
        "date": date_out,
        "index": 0,
        "proof": [],
        "root": leaf,
        "leaf": leaf,
        "note": "single-anchor leaf; populate metadata.mindex_record for full hash-chain verification",
    }


@verify_router.get("/{record_id}")
async def verify_integrity_record(record_id: str, db: AsyncSession = Depends(get_db_session)):
    """Lightweight verification flags from ledger.anchor (layer-2 refs + optional ed25519 metadata)."""
    r = await db.execute(
        text(
            """
            SELECT solana_signature, ordinal_inscription_id, platform_one_ref, metadata
            FROM ledger.anchor
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": record_id},
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="anchor_not_found")
    has_layer = bool(row.get("solana_signature") or row.get("ordinal_inscription_id") or row.get("platform_one_ref"))
    meta = _metadata_dict(row.get("metadata"))
    chain_ok = bool(meta.get("signature")) and bool(meta.get("public_key"))
    return {
        "valid": bool(has_layer or chain_ok),
        "has_layer_anchor": has_layer,
        "has_ed25519_in_metadata": chain_ok,
    }
