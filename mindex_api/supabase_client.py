"""
Supabase Integration Layer for MINDEX
=======================================
Uses Supabase as cloud middleware between local PostgreSQL and global access:

1. **Cloud Sync** — Replicate hot data to Supabase for CREP/web/mobile access
2. **Realtime** — Push updates to CREP map clients via Supabase Realtime
3. **Storage** — Large file storage (images, TLE sets, raw scrapes) → Supabase Storage
4. **Auth** — Supabase Auth for beta users / API key management
5. **Edge Functions** — CDN-cached responses for global low-latency
6. **Postgrest** — Direct table access for agents/MCP clients

Architecture:
    Local PostgreSQL (hot, <1ms) → Supabase (warm, <50ms global) → NAS (cold, bulk)

    Writes always go to local PostgreSQL first, then async-sync to Supabase.
    Reads: local cache → local DB → Supabase → live scrape → store locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Async Supabase client for MINDEX cloud sync and storage."""

    def __init__(self):
        self.url = getattr(settings, "supabase_url", "") or ""
        self.anon_key = getattr(settings, "supabase_anon_key", "") or ""
        self.service_key = getattr(settings, "supabase_service_role_key", "") or ""
        self._client: Optional[httpx.AsyncClient] = None
        self._enabled = bool(self.url and (self.service_key or self.anon_key))

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.url,
                headers={
                    "apikey": self.service_key or self.anon_key,
                    "Authorization": f"Bearer {self.service_key or self.anon_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                timeout=30,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # POSTGREST — Table Operations (via Supabase REST API)
    # =========================================================================

    async def select(
        self, table: str, columns: str = "*",
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100, order: Optional[str] = None,
    ) -> List[dict]:
        """Select rows from a Supabase table via PostgREST."""
        if not self._enabled:
            return []

        client = await self._get_client()
        params: Dict[str, Any] = {"select": columns, "limit": limit}

        if filters:
            for key, value in filters.items():
                params[key] = f"eq.{value}"

        if order:
            params["order"] = order

        try:
            resp = await client.get(f"/rest/v1/{table}", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Supabase select {table} error: {e}")
            return []

    async def upsert(self, table: str, records: List[dict], on_conflict: str = "id") -> bool:
        """Upsert records to a Supabase table."""
        if not self._enabled or not records:
            return False

        client = await self._get_client()
        try:
            resp = await client.post(
                f"/rest/v1/{table}",
                json=records,
                headers={
                    "Prefer": "resolution=merge-duplicates",
                    "on_conflict": on_conflict,
                },
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Supabase upsert {table} error: {e}")
            return False

    async def rpc(self, function_name: str, params: Optional[dict] = None) -> Any:
        """Call a Supabase RPC (stored procedure / edge function)."""
        if not self._enabled:
            return None

        client = await self._get_client()
        try:
            resp = await client.post(f"/rest/v1/rpc/{function_name}", json=params or {})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Supabase RPC {function_name} error: {e}")
            return None

    # =========================================================================
    # REALTIME — Push Updates to CREP Clients
    # =========================================================================

    async def broadcast(self, channel: str, event: str, payload: dict) -> bool:
        """Broadcast a realtime event to Supabase Realtime channel.

        Used to push live data updates to CREP map clients:
        - New earthquake detected → broadcast to "earth_events" channel
        - Aircraft position update → broadcast to "transport" channel
        - Myca response ready → broadcast to "myca" channel
        """
        if not self._enabled:
            return False

        client = await self._get_client()
        try:
            resp = await client.post(
                "/realtime/v1/api/broadcast",
                json={
                    "channel": channel,
                    "event": event,
                    "payload": payload,
                },
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Supabase broadcast error: {e}")
            return False

    # =========================================================================
    # STORAGE — File Operations (images, TLEs, raw scrapes)
    # =========================================================================

    async def upload_file(
        self, bucket: str, path: str, data: bytes,
        content_type: str = "application/octet-stream",
    ) -> Optional[str]:
        """Upload a file to Supabase Storage. Returns public URL."""
        if not self._enabled:
            return None

        client = await self._get_client()
        try:
            resp = await client.post(
                f"/storage/v1/object/{bucket}/{path}",
                content=data,
                headers={"Content-Type": content_type},
            )
            resp.raise_for_status()
            return f"{self.url}/storage/v1/object/public/{bucket}/{path}"
        except Exception as e:
            logger.error(f"Supabase upload error: {e}")
            return None

    async def get_public_url(self, bucket: str, path: str) -> str:
        """Get public URL for a stored file."""
        return f"{self.url}/storage/v1/object/public/{bucket}/{path}"

    async def download_file(self, bucket: str, path: str) -> Optional[bytes]:
        """Download a file from Supabase Storage."""
        if not self._enabled:
            return None

        client = await self._get_client()
        try:
            resp = await client.get(f"/storage/v1/object/{bucket}/{path}")
            resp.raise_for_status()
            return resp.content
        except Exception:
            return None

    # =========================================================================
    # SYNC — Replicate Local Data to Supabase
    # =========================================================================

    async def sync_earth_entities(self, entities: List[dict]) -> int:
        """Sync earth entities to Supabase for CREP web access.

        Pushes local data to Supabase so that:
        - CREP web clients can query via PostgREST
        - Realtime subscriptions push live updates
        - Edge Functions serve cached responses globally
        """
        if not self._enabled or not entities:
            return 0

        # Transform to Supabase-friendly format
        records = []
        for entity in entities:
            records.append({
                "id": str(entity.get("id", "")),
                "domain": entity.get("domain", ""),
                "entity_type": entity.get("entity_type", ""),
                "name": entity.get("name", ""),
                "lat": entity.get("lat"),
                "lng": entity.get("lng"),
                "occurred_at": entity.get("occurred_at"),
                "source": entity.get("source"),
                "properties": json.dumps(entity.get("properties", {})),
            })

        # Batch upsert (100 at a time)
        synced = 0
        for i in range(0, len(records), 100):
            batch = records[i:i + 100]
            if await self.upsert("earth_entities", batch, on_conflict="id"):
                synced += len(batch)

        # Broadcast update event for CREP realtime
        if synced > 0:
            domains = list(set(e.get("domain", "") for e in entities))
            await self.broadcast("earth_updates", "sync", {
                "count": synced,
                "domains": domains,
            })

        return synced

    async def sync_search_results(self, query: str, results: Dict[str, List]) -> bool:
        """Cache search results in Supabase for global low-latency access."""
        if not self._enabled:
            return False

        # Store as a search cache entry
        cache_entry = {
            "query": query,
            "results": json.dumps(results),
            "total_count": sum(len(v) for v in results.values()),
            "cached_at": "now()",
        }

        return await self.upsert("search_cache", [cache_entry], on_conflict="query")


# Singleton instance
_supabase: Optional[SupabaseClient] = None


def get_supabase() -> SupabaseClient:
    """Get singleton Supabase client."""
    global _supabase
    if _supabase is None:
        _supabase = SupabaseClient()
    return _supabase
