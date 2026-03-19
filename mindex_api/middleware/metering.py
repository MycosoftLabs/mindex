"""
Usage Metering Middleware — records every API call for billing and audit.

Fires async background tasks to avoid adding latency to the response path:
- Increments api_keys.usage_count and last_used_at
- Upserts api_key_usage window counters
- Inserts api_key_audit log entries
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class MeteringMiddleware(BaseHTTPMiddleware):
    """Record usage for every authenticated API request."""

    def __init__(self, app, path_prefix: str = "/api/worldview"):
        super().__init__(app)
        self.path_prefix = path_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()
        response = await call_next(request)
        elapsed_ms = int((time.time() - start_time) * 1000)

        path = request.url.path

        # Only meter Worldview API paths
        if not path.startswith(self.path_prefix):
            return response

        identity = getattr(request.state, "caller_identity", None)
        if identity is None or identity.plan == "internal":
            return response

        # Fire-and-forget background task for metering
        asyncio.create_task(
            self._record_usage(
                key_id=str(identity.key_id),
                endpoint=path,
                method=request.method,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        )

        return response

    async def _record_usage(
        self,
        key_id: str,
        endpoint: str,
        method: str,
        status_code: int,
        elapsed_ms: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """Background task to record usage in the database."""
        try:
            from ..db import get_db
            from sqlalchemy import text

            # Get a fresh DB session
            async for db in get_db():
                try:
                    now = datetime.now(timezone.utc)

                    # 1. Update api_keys usage counter and last_used_at
                    await db.execute(
                        text("""
                            UPDATE api_keys
                            SET usage_count = usage_count + 1,
                                last_used_at = :now,
                                updated_at = :now
                            WHERE id = :key_id::uuid
                        """),
                        {"key_id": key_id, "now": now},
                    )

                    # 2. Upsert api_key_usage window counter
                    minute_window = now.replace(second=0, microsecond=0)
                    await db.execute(
                        text("""
                            INSERT INTO api_key_usage (key_id, window_start, window_type, request_count)
                            VALUES (:key_id::uuid, :window_start, 'minute', 1)
                            ON CONFLICT (key_id, window_start, window_type)
                            DO UPDATE SET request_count = api_key_usage.request_count + 1
                        """),
                        {"key_id": key_id, "window_start": minute_window},
                    )

                    # 3. Insert audit log entry
                    import json
                    await db.execute(
                        text("""
                            INSERT INTO api_key_audit (key_id, action, ip_address, user_agent, endpoint, metadata)
                            VALUES (:key_id::uuid, 'request', :ip::inet, :ua, :endpoint, :meta::jsonb)
                        """),
                        {
                            "key_id": key_id,
                            "ip": ip_address,
                            "ua": user_agent,
                            "endpoint": endpoint,
                            "meta": json.dumps({
                                "method": method,
                                "status_code": status_code,
                                "elapsed_ms": elapsed_ms,
                            }),
                        },
                    )

                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.warning(f"Metering write failed: {e}")
                break  # only need one session
        except Exception as e:
            logger.warning(f"Metering task error: {e}")
