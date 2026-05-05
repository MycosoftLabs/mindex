"""Solana RPC helpers — mainnet; no secrets in code."""
from __future__ import annotations

from typing import Any, Optional

import httpx


async def fetch_solana_health(rpc_url: str, timeout: float = 4.0) -> dict[str, Any]:
    """
    Call getHealth + getSlot on JSON-RPC. Returns only fields observable from RPC (no fabricated slot).
    """
    out: dict[str, Any] = {
        "connected": False,
        "network": "mainnet-beta",
        "rpc_url": rpc_url,
        "slot": None,
        "block_height": None,
        "health": "unknown",
        "estimated_fee_sol": None,
    }
    if not rpc_url or not rpc_url.startswith("http"):
        return out
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            h = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "getHealth", "params": []},
            )
            if h.status_code == 200:
                body = h.json()
                res = body.get("result")
                out["health"] = "ok" if res == "ok" else str(res)
                out["connected"] = res == "ok"
            s = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 2, "method": "getSlot", "params": []},
            )
            if s.status_code == 200:
                slot = s.json().get("result")
                if isinstance(slot, int):
                    out["slot"] = slot
                    out["block_height"] = slot
    except Exception:
        out["connected"] = False
        out["health"] = "error"
    return out


async def fetch_recent_prioritization_fees(rpc_url: str) -> Optional[float]:
    """Best-effort micro-SOL fee hint from getRecentPrioritizationFees."""
    if not rpc_url or not rpc_url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "getRecentPrioritizationFees",
                    "params": [],
                },
            )
            if r.status_code != 200:
                return None
            arr = r.json().get("result") or []
            if not arr:
                return None
            first = arr[0]
            if isinstance(first, dict) and isinstance(first.get("prioritizationFee"), int):
                return max(0.0, first["prioritizationFee"] / 1_000_000_000)
    except Exception:
        return None
    return None
