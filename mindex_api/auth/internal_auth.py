"""
Internal service token authentication for service-to-service APIs.

Tokens are HMAC-SHA256 signed: base64(service_name:timestamp:signature)
where signature = HMAC-SHA256(secret, service_name + ":" + timestamp).

Validates against MINDEX_INTERNAL_SECRET env var with 5-minute replay window.
Also accepts simple pre-shared tokens from MINDEX_INTERNAL_TOKENS for simpler setups.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .models import CallerIdentity

logger = logging.getLogger(__name__)

_internal_token_header = APIKeyHeader(name="X-Internal-Token", auto_error=False)

# Also accept X-API-Key for backward compatibility during migration
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_REPLAY_WINDOW_SECONDS = 300  # 5 minutes


def _validate_hmac_token(token: str, secret: str) -> Optional[str]:
    """
    Validate an HMAC-signed service token.

    Token format: base64(service_name:timestamp:hex_signature)
    Returns service_name if valid, None otherwise.
    """
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        parts = decoded.split(":", 2)
        if len(parts) != 3:
            return None

        service_name, timestamp_str, provided_sig = parts
        timestamp = int(timestamp_str)

        # Check replay window
        now = int(time.time())
        if abs(now - timestamp) > _REPLAY_WINDOW_SECONDS:
            logger.warning(f"Internal token replay rejected: service={service_name}, age={now - timestamp}s")
            return None

        # Verify HMAC signature
        message = f"{service_name}:{timestamp_str}"
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if hmac.compare_digest(provided_sig, expected_sig):
            return service_name

        return None
    except Exception:
        return None


def _generate_internal_token(service_name: str, secret: str) -> str:
    """
    Generate an HMAC-signed internal service token.

    Utility for other services to generate valid tokens.
    """
    timestamp = str(int(time.time()))
    message = f"{service_name}:{timestamp}"
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{service_name}:{timestamp}:{signature}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


async def require_internal_token(
    internal_token: Optional[str] = Depends(_internal_token_header),
    api_key: Optional[str] = Depends(_api_key_header),
) -> CallerIdentity:
    """
    Validate an internal service token for service-to-service API access.

    Supports two modes:
    1. HMAC-signed tokens (preferred): validated against MINDEX_INTERNAL_SECRET
    2. Pre-shared tokens (simple): validated against MINDEX_INTERNAL_TOKENS list

    Also accepts X-API-Key during the backward-compatibility migration window
    if it matches the legacy API_KEYS env var.
    """
    from ..config import settings

    token = internal_token

    # During migration: also accept X-API-Key if it matches legacy API_KEYS
    if token is None and api_key is not None:
        if settings.api_keys and api_key in settings.api_keys:
            logger.warning("Internal API accessed via legacy X-API-Key — migrate to X-Internal-Token")
            return CallerIdentity(
                key_id=uuid.UUID(int=0),
                owner_id=uuid.UUID(int=0),
                user_type="service",
                plan="internal",
                scopes=["internal:all"],
                rate_limit_per_minute=999999,
                rate_limit_per_day=999999,
                service="legacy",
            )

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing internal service token. Provide X-Internal-Token header.",
        )

    # Mode 1: Check pre-shared tokens (simple list)
    internal_tokens = settings.internal_tokens
    if internal_tokens and token in internal_tokens:
        return CallerIdentity(
            key_id=uuid.UUID(int=0),
            owner_id=uuid.UUID(int=0),
            user_type="service",
            plan="internal",
            scopes=["internal:all"],
            rate_limit_per_minute=999999,
            rate_limit_per_day=999999,
            service="internal",
        )

    # Mode 2: Check HMAC-signed tokens
    internal_secret = settings.internal_auth_secret
    if internal_secret:
        service_name = _validate_hmac_token(token, internal_secret)
        if service_name:
            return CallerIdentity(
                key_id=uuid.UUID(int=0),
                owner_id=uuid.UUID(int=0),
                user_type="service",
                plan="internal",
                scopes=["internal:all"],
                rate_limit_per_minute=999999,
                rate_limit_per_day=999999,
                service=service_name,
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal service token.",
    )
