from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def hash_dataset(payload: bytes) -> bytes:
    """Return a SHA-256 hash of the payload."""
    return hashlib.sha256(payload).digest()


async def record_hypergraph_anchor(
    db: AsyncSession,
    *,
    ip_asset_id: UUID,
    anchor_hash: bytes,
    metadata: Optional[Dict[str, Any]] = None,
    sample_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """
    Persist a Hypergraph anchor record referencing an IP asset.

    Real Hypergraph anchoring will be integrated here later; for now we store the hash.
    """
    payload = {
        "ip_asset_id": str(ip_asset_id),
        "sample_id": str(sample_id) if sample_id else None,
        "anchor_hash": anchor_hash,
        "metadata": json.dumps(metadata or {}),
    }
    stmt = text(
        """
        INSERT INTO ledger.hypergraph_anchor (
            ip_asset_id,
            sample_id,
            anchor_hash,
            metadata
        )
        VALUES (:ip_asset_id::uuid, :sample_id::uuid, :anchor_hash, :metadata::jsonb)
        RETURNING id, sample_id, anchor_hash, metadata, anchored_at
        """
    )
    result = await db.execute(stmt, payload)
    row = result.mappings().one()
    return {
        "id": row["id"],
        "sample_id": row["sample_id"],
        "anchor_hash": row["anchor_hash"].hex(),
        "metadata": row["metadata"],
        "anchored_at": row["anchored_at"],
    }
