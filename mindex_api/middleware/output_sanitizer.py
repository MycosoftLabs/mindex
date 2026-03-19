"""
Output Sanitizer Middleware — prevents internal data leakage through the Worldview API.

Recursively walks JSON response bodies and:
- Strips keys matching a denylist (api_key, secret, token, hash, etc.)
- Strips values that look like internal URLs (localhost, private IPs)
- Detects and redacts prompt injection patterns in string fields
- Enforces maximum field lengths
- Logs sanitization events
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Keys that should never appear in Worldview API responses
DENIED_KEYS = frozenset({
    "api_key", "api_key_hash", "api_key_prefix", "key_hash", "key_prefix",
    "secret", "token", "password", "passwd", "credential",
    "internal_id", "internal_token", "service_token",
    "device_token", "device_secret",
    "system_prompt", "system_instruction", "instruction",
    "automation_rule", "command_queue", "command_payload",
    "supabase_user_id", "supabase_url", "supabase_key",
    "stripe_customer_id", "stripe_subscription_id",
    "session_id", "user_id",
    "db_dsn", "connection_string", "dsn",
    "stack_trace", "traceback",
})

# Partial key patterns (matched with 'in')
DENIED_KEY_PATTERNS = (
    "_secret", "_token", "_password", "_hash",
    "_credential", "_private",
)

# Regex patterns for values that should be redacted
INTERNAL_URL_PATTERN = re.compile(
    r"(https?://)?(localhost|127\.0\.0\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)(:\d+)?",
    re.IGNORECASE,
)

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|endoftext\|>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"ASSISTANT\s*:", re.IGNORECASE),
    re.compile(r"USER\s*:", re.IGNORECASE),
]

# Maximum string field length in output (prevent data exfiltration via oversized fields)
MAX_STRING_LENGTH = 50000


def _is_denied_key(key: str) -> bool:
    """Check if a key should be stripped from output."""
    key_lower = key.lower()
    if key_lower in DENIED_KEYS:
        return True
    for pattern in DENIED_KEY_PATTERNS:
        if pattern in key_lower:
            return True
    return False


def _sanitize_string(value: str) -> str:
    """Sanitize a string value."""
    # Truncate oversized strings
    if len(value) > MAX_STRING_LENGTH:
        value = value[:MAX_STRING_LENGTH] + "... [truncated]"

    # Redact internal URLs
    value = INTERNAL_URL_PATTERN.sub("[internal-url-redacted]", value)

    # Redact prompt injection patterns
    for pattern in PROMPT_INJECTION_PATTERNS:
        value = pattern.sub("[content-filtered]", value)

    return value


def sanitize_value(obj: Any, depth: int = 0) -> Any:
    """Recursively sanitize a value, stripping denied keys and redacting values."""
    if depth > 20:  # Prevent infinite recursion
        return obj

    if isinstance(obj, dict):
        return {
            k: sanitize_value(v, depth + 1)
            for k, v in obj.items()
            if not _is_denied_key(k)
        }
    elif isinstance(obj, list):
        return [sanitize_value(item, depth + 1) for item in obj]
    elif isinstance(obj, str):
        return _sanitize_string(obj)
    else:
        return obj


class OutputSanitizerMiddleware(BaseHTTPMiddleware):
    """Sanitize JSON response bodies on the Worldview API to prevent data leakage."""

    def __init__(self, app, path_prefix: str = "/api/worldview"):
        super().__init__(app)
        self.path_prefix = path_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Only sanitize Worldview API responses
        if not path.startswith(self.path_prefix):
            return await call_next(request)

        response = await call_next(request)

        # Only sanitize JSON responses
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read and sanitize the response body
        try:
            body = b""
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    body += chunk.encode("utf-8")
                else:
                    body += chunk

            data = json.loads(body)
            sanitized = sanitize_value(data)

            # Check if anything was actually sanitized
            sanitized_json = json.dumps(sanitized)
            if len(sanitized_json) != len(body):
                logger.info(f"Worldview output sanitized: {path} (original={len(body)}B, sanitized={len(sanitized_json)}B)")

            return JSONResponse(
                content=sanitized,
                status_code=response.status_code,
                headers=dict(response.headers),
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Output sanitizer error on {path}: {e}")
            return response
