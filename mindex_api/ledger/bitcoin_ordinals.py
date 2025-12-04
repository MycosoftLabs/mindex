from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def content_hash(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


async def record_bitcoin_ordinal(
    db: AsyncSession,
    *,
    ip_asset_id: UUID,
    payload: bytes,
    inscription_id: str,
    inscription_address: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stmt = text(
        """
        INSERT INTO ledger.bitcoin_ordinal (
            ip_asset_id,
            content_hash,
            inscription_id,
            inscription_address,
            metadata
        )
        VALUES (
            :ip_asset_id::uuid,
            :content_hash,
            :inscription_id,
            :inscription_address,
            :metadata::jsonb
        )
        RETURNING id, content_hash, inscription_id, inscription_address, inscribed_at, metadata
        """
    )
    params = {
        "ip_asset_id": str(ip_asset_id),
        "content_hash": content_hash(payload),
        "inscription_id": inscription_id,
        "inscription_address": inscription_address,
        "metadata": json.dumps(metadata or {}),
    }
    result = await db.execute(stmt, params)
    row = result.mappings().one()
    data = dict(row)
    data["content_hash"] = row["content_hash"].hex()
    return data
