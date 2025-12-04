from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record_solana_binding(
    db: AsyncSession,
    *,
    ip_asset_id: UUID,
    mint_address: str,
    token_account: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stmt = text(
        """
        INSERT INTO ledger.solana_binding (
            ip_asset_id,
            mint_address,
            token_account,
            metadata
        )
        VALUES (:ip_asset_id::uuid, :mint_address, :token_account, :metadata::jsonb)
        RETURNING id, mint_address, token_account, bound_at, metadata
        """
    )
    params = {
        "ip_asset_id": str(ip_asset_id),
        "mint_address": mint_address,
        "token_account": token_account,
        "metadata": json.dumps(metadata or {}),
    }
    result = await db.execute(stmt, params)
    return dict(result.mappings().one())
