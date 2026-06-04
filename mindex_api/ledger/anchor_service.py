"""Multi-chain anchor orchestration for ledger.anchor rows."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..ledger.bitcoin_ordinals import record_bitcoin_ordinal
from ..ledger.dag import insert_dag_node
from ..ledger.hypergraph_client import submit_hypergraph_anchor
from ..ledger.op_return import op_return_from_content_hash
from ..ledger.platform_one_client import ping_platform_one
from ..ledger.solana import record_solana_binding

LedgerKind = Literal["hypergraph", "solana", "bitcoin"]


async def _load_anchor_row(db: AsyncSession, record_id: str) -> Optional[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT id::text, entity_type, entity_id::text,
                   encode(content_hash, 'hex') AS content_hash_hex,
                   tier, solana_signature, ordinal_inscription_id,
                   platform_one_ref, metadata
            FROM ledger.anchor
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": record_id},
    )
    row = r.mappings().first()
    return dict(row) if row else None


async def _resolve_ip_asset_id(db: AsyncSession, entity_type: str, entity_id: str) -> Optional[str]:
    if entity_type != "taxon":
        return None
    r = await db.execute(
        text(
            """
            SELECT id::text FROM ip.ip_asset
            WHERE taxon_id = :tid::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"tid": entity_id},
    )
    row = r.mappings().first()
    return row["id"] if row else None


async def anchor_record_on_ledger(
    db: AsyncSession,
    *,
    record_id: str,
    ledger: LedgerKind,
) -> dict[str, Any]:
    row = await _load_anchor_row(db, record_id)
    if not row:
        return {"ok": False, "record_id": record_id, "message": "anchor_not_found"}

    ch_hex = str(row["content_hash_hex"] or "")
    if len(ch_hex) != 64:
        return {"ok": False, "record_id": record_id, "message": "invalid_content_hash"}

    raw = bytes.fromhex(ch_hex)
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}

    ip_asset_id = await _resolve_ip_asset_id(db, row["entity_type"], row["entity_id"])
    tx_id: Optional[str] = None
    detail: dict[str, Any] = {"ledger": ledger, "record_id": record_id}

    if ledger == "hypergraph":
        dag = await insert_dag_node(
            db,
            content_hash=raw,
            epoch=0,
            metadata={"anchor_id": record_id, "entity_type": row["entity_type"]},
        )
        p1 = await ping_platform_one()
        p1_ref = None
        if p1.get("reachable"):
            p1_ref = f"p1:health:{p1.get('status_code')}"
        hg_meta = {
            "anchor_id": record_id,
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "platform_one_ref": p1_ref,
            "dag_node_id": str(dag["id"]),
        }
        remote = await submit_hypergraph_anchor(ch_hex, hg_meta)
        if ip_asset_id:
            from ..ledger.hypergraph import record_hypergraph_anchor

            await record_hypergraph_anchor(
                db,
                ip_asset_id=uuid.UUID(ip_asset_id),
                anchor_hash=raw,
                metadata={**hg_meta, "remote": remote},
            )
        meta["hypergraph"] = {**hg_meta, "remote": remote}
        tx_id = f"hg:dag:{dag['id']}"
        await db.execute(
            text(
                """
                UPDATE ledger.anchor
                SET hypergraph_node_id = :nid::uuid,
                    platform_one_ref = COALESCE(:p1, platform_one_ref),
                    metadata = CAST(:meta AS jsonb),
                    tier = COALESCE(tier, 'hypergraph')
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "nid": str(dag["id"]),
                "p1": p1_ref,
                "meta": json.dumps(meta),
                "id": record_id,
            },
        )

    elif ledger == "solana":
        mint = (settings.myca_solana_mint or "").strip()
        if not mint:
            return {
                "ok": False,
                "record_id": record_id,
                "message": "MYCA_SOLANA_MINT not configured on MINDEX host",
            }
        binding_meta = {
            "anchor_id": record_id,
            "content_hash_hex": ch_hex,
            "token": "MYCA",
            "mint": mint,
            "binding_type": "spl_token_metadata",
        }
        if ip_asset_id:
            binding = await record_solana_binding(
                db,
                ip_asset_id=uuid.UUID(ip_asset_id),
                mint_address=mint,
                metadata=binding_meta,
            )
            tx_id = f"solana:binding:{binding['id']}"
        else:
            tx_id = f"solana:pending:{ch_hex[:16]}"
        meta["solana"] = binding_meta
        await db.execute(
            text(
                """
                UPDATE ledger.anchor
                SET solana_signature = :sig,
                    metadata = CAST(:meta AS jsonb)
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"sig": tx_id, "meta": json.dumps(meta), "id": record_id},
        )

    elif ledger == "bitcoin":
        op = op_return_from_content_hash(ch_hex)
        inscription_id = f"op_return:{op['op_return_hex'][:32]}"
        if ip_asset_id:
            ord_row = await record_bitcoin_ordinal(
                db,
                ip_asset_id=uuid.UUID(ip_asset_id),
                payload=raw,
                inscription_id=inscription_id,
                metadata={"op_return": op, "anchor_id": record_id},
            )
            tx_id = f"btc:ordinal:{ord_row['id']}"
        else:
            tx_id = inscription_id
        meta["bitcoin"] = {"op_return": op, "inscription_id": inscription_id}
        await db.execute(
            text(
                """
                UPDATE ledger.anchor
                SET ordinal_inscription_id = :oid,
                    metadata = CAST(:meta AS jsonb)
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"oid": inscription_id, "meta": json.dumps(meta), "id": record_id},
        )

    await db.commit()
    return {
        "ok": True,
        "ledger": ledger,
        "record_id": record_id,
        "tx_id": tx_id,
        "message": "anchored_to_mindex",
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
