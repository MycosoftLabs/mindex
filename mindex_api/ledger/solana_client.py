"""Solana RPC helpers — mainnet; no secrets in code."""
from __future__ import annotations

from typing import Any, Iterable, Optional

import httpx

# Public read-only fallback when QuickNode or other paid RPC is disabled.
DEFAULT_SOLANA_RPC_FALLBACKS: tuple[str, ...] = (
    "https://api.mainnet-beta.solana.com",
)


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


async def fetch_spl_mint_summary(rpc_url: str, mint_address: str, timeout: float = 5.0) -> dict[str, Any]:
    """Token supply + decimals for MYCA / DAO mint via getTokenSupply."""
    out: dict[str, Any] = {
        "mint": mint_address,
        "configured": bool(mint_address),
        "supply": None,
        "decimals": None,
        "connected": False,
    }
    if not rpc_url or not mint_address:
        return out
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "getTokenSupply",
                    "params": [mint_address],
                },
            )
            if r.status_code != 200:
                return out
            value = (r.json().get("result") or {}).get("value") or {}
            out["connected"] = True
            out["supply"] = value.get("uiAmountString") or value.get("amount")
            out["decimals"] = value.get("decimals")
    except Exception:
        out["connected"] = False
    return out


def _dedupe_rpc_urls(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        u = (raw or "").strip()
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


async def resolve_working_solana_rpc(
    urls: Iterable[str],
    *,
    timeout: float = 4.0,
) -> tuple[str, dict[str, Any]]:
    """
    Try RPC URLs in order; return the first that responds healthy + last health snapshot.
    """
    candidates = _dedupe_rpc_urls(urls)
    last_health: dict[str, Any] = {
        "connected": False,
        "health": "not_configured",
        "network": "mainnet-beta",
        "rpc_url": None,
        "slot": None,
        "block_height": None,
    }
    for url in candidates:
        health = await fetch_solana_health(url, timeout=timeout)
        last_health = health
        if health.get("connected"):
            return url, health
    return "", last_health
