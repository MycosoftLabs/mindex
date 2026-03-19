"""
Request Validation Middleware — input validation for the Worldview API.

- Rejects request bodies on GET endpoints (Worldview is read-only)
- Enforces maximum query string length
- Detects suspicious query patterns (SQL injection, prompt injection)
"""

from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

MAX_QUERY_STRING_LENGTH = 2048

# Patterns that suggest SQL injection attempts
SQL_INJECTION_PATTERNS = [
    re.compile(r"(\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC)\b\s)", re.IGNORECASE),
    re.compile(r"(--|;)\s*(SELECT|DROP|INSERT|UPDATE|DELETE)", re.IGNORECASE),
    re.compile(r"'\s*(OR|AND)\s+'", re.IGNORECASE),
]

# Patterns that suggest prompt injection attempts in query params
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"<\|endoftext\|>", re.IGNORECASE),
]


class RequestValidationMiddleware(BaseHTTPMiddleware):
    """Validate incoming requests to the Worldview API."""

    def __init__(self, app, path_prefix: str = "/api/worldview"):
        super().__init__(app)
        self.path_prefix = path_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Only validate Worldview API requests
        if not path.startswith(self.path_prefix):
            return await call_next(request)

        # Reject bodies on GET/HEAD requests (Worldview is read-only)
        if request.method in ("GET", "HEAD"):
            content_length = request.headers.get("content-length", "0")
            if content_length != "0" and content_length != "":
                try:
                    if int(content_length) > 0:
                        return JSONResponse(
                            status_code=400,
                            content={"error": "Request body not allowed on GET endpoints."},
                        )
                except ValueError:
                    pass

        # Only allow GET and HEAD on Worldview (read-only)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse(
                status_code=405,
                content={"error": f"Method {request.method} not allowed on Worldview API. This API is read-only."},
            )

        # Check query string length
        query_string = str(request.url.query) if request.url.query else ""
        if len(query_string) > MAX_QUERY_STRING_LENGTH:
            return JSONResponse(
                status_code=400,
                content={"error": f"Query string too long (max {MAX_QUERY_STRING_LENGTH} chars)."},
            )

        # Check for SQL injection patterns in query params
        for pattern in SQL_INJECTION_PATTERNS:
            if pattern.search(query_string):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid query parameters."},
                )

        # Check for prompt injection patterns in query params
        for pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(query_string):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid query parameters."},
                )

        return await call_next(request)
