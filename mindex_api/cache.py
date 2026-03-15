"""
Redis Cache Layer for MINDEX
==============================
Provides multi-tier caching for ultra-low latency data access:

Tier 0: In-process LRU (< 0.1ms) — hot keys, recent queries
Tier 1: Redis (< 1ms) — search results, entity lookups, API responses
Tier 2: PostgreSQL (< 5ms) — full local database
Tier 3: Supabase (< 50ms) — cloud-synced data
Tier 4: Live API scrape (100-2000ms) — external sources, then cached locally

Cache key patterns:
    search:{hash(query+types)}       — unified search results
    entity:{domain}:{id}             — individual entity
    map:{layer}:{bbox_hash}          — CREP map layer tiles
    stats:{domain}                   — count/aggregate stats
    scrape:{source}:{query}          — on-demand scrape results
    myca:{conversation_id}:{turn}    — Myca response cache
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from functools import lru_cache
from typing import Any, Callable, Optional

from .config import settings

logger = logging.getLogger(__name__)

# In-process LRU cache for ultra-hot data
_lru_cache: dict = {}
_lru_timestamps: dict = {}
_LRU_MAX_SIZE = 1000
_LRU_DEFAULT_TTL = 30  # seconds


class RedisCache:
    """Async Redis cache with automatic fallback to in-process LRU."""

    def __init__(self):
        self._redis = None
        self._enabled = False
        self._redis_url = getattr(settings, "redis_url", "") or ""

    async def connect(self):
        """Lazily connect to Redis."""
        if self._redis is not None:
            return

        if not self._redis_url:
            logger.info("Redis URL not configured — using in-process LRU cache only")
            return

        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=2,
                retry_on_timeout=True,
            )
            # Test connection
            await self._redis.ping()
            self._enabled = True
            logger.info(f"Redis connected: {self._redis_url.split('@')[-1] if '@' in self._redis_url else self._redis_url}")
        except Exception as e:
            logger.warning(f"Redis unavailable, falling back to LRU: {e}")
            self._redis = None
            self._enabled = False

    async def close(self):
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._enabled = False

    @property
    def connected(self) -> bool:
        return self._enabled

    # =========================================================================
    # CORE OPS
    # =========================================================================

    async def get(self, key: str) -> Optional[str]:
        """Get a cached value. Checks LRU first, then Redis."""
        # Tier 0: In-process LRU
        if key in _lru_cache:
            ts = _lru_timestamps.get(key, 0)
            if time.time() - ts < _LRU_DEFAULT_TTL:
                return _lru_cache[key]
            else:
                _lru_cache.pop(key, None)
                _lru_timestamps.pop(key, None)

        # Tier 1: Redis
        if self._enabled:
            try:
                val = await self._redis.get(key)
                if val is not None:
                    # Promote to LRU
                    _set_lru(key, val)
                return val
            except Exception as e:
                logger.debug(f"Redis get error: {e}")

        return None

    async def set(self, key: str, value: str, ttl: int = 300) -> bool:
        """Set a cached value in both LRU and Redis."""
        _set_lru(key, value)

        if self._enabled:
            try:
                await self._redis.setex(key, ttl, value)
                return True
            except Exception as e:
                logger.debug(f"Redis set error: {e}")

        return False

    async def delete(self, key: str) -> bool:
        """Delete a cached key."""
        _lru_cache.pop(key, None)
        _lru_timestamps.pop(key, None)

        if self._enabled:
            try:
                await self._redis.delete(key)
                return True
            except Exception:
                pass
        return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Get and deserialize a JSON cached value."""
        val = await self.get(key)
        if val:
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    async def set_json(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Serialize and cache a JSON value."""
        try:
            return await self.set(key, json.dumps(value, default=str), ttl)
        except (TypeError, ValueError):
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if key in _lru_cache:
            return True
        if self._enabled:
            try:
                return bool(await self._redis.exists(key))
            except Exception:
                pass
        return False

    # =========================================================================
    # SEARCH CACHE
    # =========================================================================

    async def cache_search(self, query: str, types: str, results: dict, ttl: int = 120) -> bool:
        """Cache unified search results."""
        key = f"search:{_hash(query + '|' + types)}"
        return await self.set_json(key, results, ttl)

    async def get_cached_search(self, query: str, types: str) -> Optional[dict]:
        """Get cached search results."""
        key = f"search:{_hash(query + '|' + types)}"
        return await self.get_json(key)

    # =========================================================================
    # ENTITY CACHE
    # =========================================================================

    async def cache_entity(self, domain: str, entity_id: str, data: dict, ttl: int = 600) -> bool:
        """Cache an individual entity."""
        key = f"entity:{domain}:{entity_id}"
        return await self.set_json(key, data, ttl)

    async def get_cached_entity(self, domain: str, entity_id: str) -> Optional[dict]:
        """Get a cached entity."""
        key = f"entity:{domain}:{entity_id}"
        return await self.get_json(key)

    # =========================================================================
    # MAP TILE CACHE
    # =========================================================================

    async def cache_map_layer(
        self, layer: str, bbox: tuple, data: list, ttl: int = 60
    ) -> bool:
        """Cache CREP map layer results for a bounding box."""
        key = f"map:{layer}:{_hash(str(bbox))}"
        return await self.set_json(key, data, ttl)

    async def get_cached_map_layer(self, layer: str, bbox: tuple) -> Optional[list]:
        """Get cached map layer data."""
        key = f"map:{layer}:{_hash(str(bbox))}"
        return await self.get_json(key)

    # =========================================================================
    # SCRAPE TRACKING
    # =========================================================================

    async def mark_scraped(self, source: str, key: str, ttl: int = 86400) -> bool:
        """Mark a source+key as already scraped (dedup)."""
        cache_key = f"scraped:{source}:{_hash(key)}"
        return await self.set(cache_key, "1", ttl)

    async def is_scraped(self, source: str, key: str) -> bool:
        """Check if a source+key has been scraped recently."""
        cache_key = f"scraped:{source}:{_hash(key)}"
        return await self.exists(cache_key)

    # =========================================================================
    # STATS CACHE
    # =========================================================================

    async def cache_stats(self, domain: str, stats: dict, ttl: int = 300) -> bool:
        key = f"stats:{domain}"
        return await self.set_json(key, stats, ttl)

    async def get_cached_stats(self, domain: str) -> Optional[dict]:
        key = f"stats:{domain}"
        return await self.get_json(key)

    # =========================================================================
    # BULK OPS
    # =========================================================================

    async def flush_domain(self, domain: str) -> int:
        """Flush all cache entries for a domain (after data sync)."""
        if not self._enabled:
            return 0

        try:
            keys = []
            async for key in self._redis.scan_iter(f"*:{domain}:*"):
                keys.append(key)
            if keys:
                await self._redis.delete(*keys)
            return len(keys)
        except Exception as e:
            logger.debug(f"Redis flush error: {e}")
            return 0


# =========================================================================
# HELPERS
# =========================================================================

def _hash(value: str) -> str:
    """Short hash for cache keys."""
    return hashlib.md5(value.encode()).hexdigest()[:12]


def _set_lru(key: str, value: str):
    """Set in LRU with eviction."""
    if len(_lru_cache) >= _LRU_MAX_SIZE:
        # Evict oldest
        oldest_key = min(_lru_timestamps, key=_lru_timestamps.get)
        _lru_cache.pop(oldest_key, None)
        _lru_timestamps.pop(oldest_key, None)

    _lru_cache[key] = value
    _lru_timestamps[key] = time.time()


# =========================================================================
# SINGLETON
# =========================================================================

_cache: Optional[RedisCache] = None


def get_cache() -> RedisCache:
    """Get singleton cache instance."""
    global _cache
    if _cache is None:
        _cache = RedisCache()
    return _cache
