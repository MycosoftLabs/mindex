"""Hypergraph DAG persistence helpers."""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_dag_node(
    db: AsyncSession,
    *,
    content_hash: bytes,
    epoch: int = 0,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    stmt = text(
        """
        INSERT INTO ledger.dag_node (content_hash, parent_hashes, epoch, metadata)
        VALUES (:content_hash, '{}'::bytea[], :epoch, CAST(:metadata AS jsonb))
        RETURNING id, encode(content_hash, 'hex') AS content_hash_hex, epoch, metadata, created_at
        """
    )
    params = {
        "content_hash": content_hash,
        "epoch": epoch,
        "metadata": json.dumps(metadata or {}),
    }
    result = await db.execute(stmt, params)
    row = result.mappings().one()
    return dict(row)


async def fetch_dag_path(db: AsyncSession, content_hash_hex: str) -> list[dict[str, Any]]:
    h = content_hash_hex.lower().lstrip("0x")
    stmt = text(
        """
        SELECT id, encode(content_hash, 'hex') AS content_hash_hex, epoch, metadata, created_at
        FROM ledger.dag_node
        WHERE encode(content_hash, 'hex') = :h
        ORDER BY created_at ASC
        LIMIT 50
        """
    )
    result = await db.execute(stmt, {"h": h})
    return [dict(r) for r in result.mappings().all()]
