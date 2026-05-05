"""Bitcoin / mempool.space read-only helpers for Ordinals anchoring prep."""
from __future__ import annotations

from typing import Any, Optional

import httpx

MEMPOOL_API = "https://mempool.space/api"


async def fetch_bitcoin_chain_tip(timeout: float = 4.0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "connected": False,
        "network": "mainnet",
        "api_url": MEMPOOL_API,
        "block_height": 0,
        "mempool_size": 0,
        "fee_rates": {"fastest": 0, "half_hour": 0, "hour": 0, "economy": 0},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{MEMPOOL_API}/blocks/tip/height")
            if resp.status_code == 200:
                out["block_height"] = int(resp.text)
                out["connected"] = True
            fees_resp = await client.get(f"{MEMPOOL_API}/v1/fees/recommended")
            if fees_resp.status_code == 200:
                fee_rates = fees_resp.json()
                out["fee_rates"] = {
                    "fastest": fee_rates.get("fastestFee", 0),
                    "half_hour": fee_rates.get("halfHourFee", 0),
                    "hour": fee_rates.get("hourFee", 0),
                    "economy": fee_rates.get("economyFee", 0),
                }
    except Exception:
        pass
    return out


async def ordinals_readiness(wallet_hint: Optional[str]) -> dict[str, Any]:
    """Non-custodial readiness flags — wallet must be configured in env on signing hosts."""
    return {
        "wallet_configured": bool(wallet_hint and len(wallet_hint.strip()) > 0),
        "network": "mainnet",
    }
