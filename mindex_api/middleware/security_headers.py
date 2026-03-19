"""
Security Headers Middleware — adds protective headers to Worldview API responses.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to Worldview API responses."""

    def __init__(self, app, path_prefix: str = "/api/worldview"):
        super().__init__(app)
        self.path_prefix = path_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path
        if path.startswith(self.path_prefix):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-API-Zone"] = "worldview"
        elif "/internal" in path:
            response.headers["X-API-Zone"] = "internal"

        return response
