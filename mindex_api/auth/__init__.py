"""
Auth Package — MINDEX API Compartmentalized Authentication

Two auth mechanisms:
- Worldview API keys: DB-backed per-user keys for paying users (humans & agents)
- Internal service tokens: HMAC-signed tokens for service-to-service (MAS, NLM, devices)
"""

from .models import CallerIdentity
from .api_key_auth import require_worldview_key
from .internal_auth import require_internal_token

__all__ = [
    "CallerIdentity",
    "require_worldview_key",
    "require_internal_token",
]
