"""
Rate Limiting Middleware — Redis sliding window counters.

Uses per-key rate limits from CallerIdentity (set by auth dependency).
Falls back to in-process counters if Redis is unavailable.
Internal service tokens bypass rate limiting entirely.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# In-process fallback counters when Redis is unavailable
_local_minute_counters: dict[str, list[float]] = defaultdict(list)
_local_day_counters: dict[str, int] = defaultdict(int)
_local_day_key: str = ""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-key rate limiting for the Worldview API."""

    def __init__(self, app, path_prefix: str = "/api/worldview"):
        super().__init__(app)
        self.path_prefix = path_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Only rate-limit Worldview API paths
        if not path.startswith(self.path_prefix):
            return await call_next(request)

        # Get caller identity from request state (set by auth dependency)
        identity = getattr(request.state, "caller_identity", None)
        if identity is None:
            # Auth dependency hasn't run yet — let it through, auth will catch it
            return await call_next(request)

        # Internal services bypass rate limiting
        if identity.plan == "internal":
            return await call_next(request)

        key_id = str(identity.key_id)

        # Try Redis first, fall back to local counters
        from ..config import settings

        minute_count, day_count = await self._get_counts(key_id, settings)

        # Check per-minute limit
        if minute_count >= identity.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "detail": f"Per-minute limit of {identity.rate_limit_per_minute} exceeded.",
                    "retry_after": 60,
                },
                headers={"Retry-After": "60"},
            )

        # Check per-day limit
        if day_count >= identity.rate_limit_per_day:
            # Calculate seconds until midnight UTC
            now = datetime.now(timezone.utc)
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            seconds_until_reset = int((midnight + timedelta(days=1) - now).total_seconds())
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "detail": f"Daily limit of {identity.rate_limit_per_day} exceeded.",
                    "retry_after": seconds_until_reset,
                },
                headers={"Retry-After": str(seconds_until_reset)},
            )

        # Increment counters
        await self._increment(key_id, settings)

        # Add rate limit headers to response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit-Minute"] = str(identity.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining-Minute"] = str(max(0, identity.rate_limit_per_minute - minute_count - 1))
        response.headers["X-RateLimit-Limit-Day"] = str(identity.rate_limit_per_day)
        response.headers["X-RateLimit-Remaining-Day"] = str(max(0, identity.rate_limit_per_day - day_count - 1))

        return response

    async def _get_counts(self, key_id: str, settings) -> tuple[int, int]:
        """Get current request counts (minute + day)."""
        try:
            return await self._redis_get_counts(key_id, settings)
        except Exception:
            return self._local_get_counts(key_id)

    async def _redis_get_counts(self, key_id: str, settings) -> tuple[int, int]:
        """Get counts from Redis sliding window."""
        if not settings.redis_url:
            raise RuntimeError("No Redis")

        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        prefix = settings.rate_limit_redis_prefix
        now = datetime.now(timezone.utc)
        minute_key = f"{prefix}:{key_id}:min:{now.strftime('%Y%m%d%H%M')}"
        day_key = f"{prefix}:{key_id}:day:{now.strftime('%Y%m%d')}"

        try:
            pipe = r.pipeline()
            pipe.get(minute_key)
            pipe.get(day_key)
            results = await pipe.execute()
            minute_count = int(results[0] or 0)
            day_count = int(results[1] or 0)
            return minute_count, day_count
        finally:
            await r.aclose()

    def _local_get_counts(self, key_id: str) -> tuple[int, int]:
        """Fallback in-process counters."""
        global _local_day_key
        now = time.time()
        today = datetime.now(timezone.utc).strftime("%Y%m%d")

        # Reset day counters if day changed
        if _local_day_key != today:
            _local_day_counters.clear()
            _local_day_key = today

        # Clean old minute entries (keep last 60 seconds)
        timestamps = _local_minute_counters[key_id]
        _local_minute_counters[key_id] = [t for t in timestamps if now - t < 60]

        return len(_local_minute_counters[key_id]), _local_day_counters[key_id]

    async def _increment(self, key_id: str, settings) -> None:
        """Increment counters."""
        try:
            await self._redis_increment(key_id, settings)
        except Exception:
            self._local_increment(key_id)

    async def _redis_increment(self, key_id: str, settings) -> None:
        """Increment Redis counters."""
        if not settings.redis_url:
            raise RuntimeError("No Redis")

        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        prefix = settings.rate_limit_redis_prefix
        now = datetime.now(timezone.utc)
        minute_key = f"{prefix}:{key_id}:min:{now.strftime('%Y%m%d%H%M')}"
        day_key = f"{prefix}:{key_id}:day:{now.strftime('%Y%m%d')}"

        try:
            pipe = r.pipeline()
            pipe.incr(minute_key)
            pipe.expire(minute_key, 120)
            pipe.incr(day_key)
            pipe.expire(day_key, 90000)
            await pipe.execute()
        finally:
            await r.aclose()

    def _local_increment(self, key_id: str) -> None:
        """Fallback in-process increment."""
        _local_minute_counters[key_id].append(time.time())
        _local_day_counters[key_id] += 1
