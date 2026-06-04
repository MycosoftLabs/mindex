"""Hypergraph decentralized DAG — health + optional anchor submit (Platform One correlation)."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from ..config import settings


async def fetch_hypergraph_status(timeout: float = 4.0) -> dict[str, Any]:
    base = str(settings.hypergraph_endpoint) if settings.hypergraph_endpoint else None
    out: dict[str, Any] = {
        "connected": False,
        "node_url": base,
        "status": "offline",
        "platform_one_correlation": bool(settings.p1_base_url and settings.p1_api_key),
        "dag_role": "decentralized_dag_anchor",
        "notes": (
            "Hypergraph DAG stores content-hash commits; Platform One (P1) receives "
            "correlated defense metadata when P1_BASE_URL is configured."
        ),
    }
    if not base:
        return out
    out["status"] = "configured"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for path in ("/health", "/api/health", "/status"):
                r = await client.get(base.rstrip("/") + path)
                if r.status_code < 500:
                    out["connected"] = r.status_code < 400
                    out["status"] = "online" if out["connected"] else "degraded"
                    out["health_status_code"] = r.status_code
                    break
    except Exception:
        out["connected"] = False
        out["status"] = "unreachable"
    return out


async def submit_hypergraph_anchor(
    content_hash_hex: str,
    metadata: Optional[dict[str, Any]] = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """POST anchor to Hypergraph node when endpoint supports it; else caller persists DB only."""
    base = str(settings.hypergraph_endpoint) if settings.hypergraph_endpoint else None
    if not base:
        return {"submitted": False, "reason": "hypergraph_endpoint_not_configured"}
    body = {
        "content_hash": content_hash_hex,
        "metadata": metadata or {},
        "platform_one_ref": metadata.get("platform_one_ref") if metadata else None,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for path in ("/anchor", "/api/anchor", "/v1/anchor"):
                r = await client.post(base.rstrip("/") + path, json=body)
                if r.status_code in (200, 201, 202):
                    try:
                        data = r.json()
                    except Exception:
                        data = {"raw": r.text[:500]}
                    return {"submitted": True, "response": data, "path": path}
    except Exception as exc:
        return {"submitted": False, "reason": str(exc)[:200]}
    return {"submitted": False, "reason": "no_anchor_route_on_node"}
