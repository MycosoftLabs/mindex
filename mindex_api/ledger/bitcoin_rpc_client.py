"""Optional Bitcoin Core RPC — local node on LAN (e.g. Raspberry Pi / miner host)."""
from __future__ import annotations

import base64
from typing import Any, Optional

import httpx


def build_op_return_script(data: bytes, max_payload: int = 80) -> dict[str, Any]:
    """Build OP_RETURN payload metadata (broadcast requires funded wallet + RPC)."""
    if len(data) > max_payload:
        data = data[:max_payload]
    hex_payload = data.hex()
    return {
        "op_return_hex": hex_payload,
        "payload_bytes": len(data),
        "max_op_return_bytes": max_payload,
        "note": "Inscriptions use ordinals envelope; OP_RETURN for compact hash commits.",
    }


async def fetch_bitcoin_rpc_status(
    rpc_url: str,
    rpc_user: Optional[str] = None,
    rpc_password: Optional[str] = None,
    timeout: float = 4.0,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "connected": False,
        "source": "bitcoin_core_rpc",
        "rpc_url": rpc_url,
        "block_height": None,
        "chain": None,
        "verification_progress": None,
        "initial_block_download": None,
    }
    if not rpc_url or not rpc_url.startswith("http"):
        return out
    auth = None
    if rpc_user and rpc_password:
        token = base64.b64encode(f"{rpc_user}:{rpc_password}".encode()).decode()
        auth = httpx.BasicAuth(rpc_user, rpc_password)
    try:
        async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
            r = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "1.0",
                    "id": "mindex",
                    "method": "getblockchaininfo",
                    "params": [],
                },
            )
            if r.status_code != 200:
                return out
            info = r.json().get("result") or {}
            out["connected"] = True
            out["block_height"] = info.get("blocks")
            out["chain"] = info.get("chain")
            out["verification_progress"] = info.get("verificationprogress")
            out["initial_block_download"] = info.get("initialblockdownload")
    except Exception:
        out["connected"] = False
    return out
