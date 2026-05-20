"""
Auth Package — MINDEX API Compartmentalized Authentication

Three auth mechanisms:
- Worldview API keys: DB-backed per-user keys for paying users (humans & agents)
- Internal service tokens: HMAC-signed tokens for service-to-service (MAS, NLM, devices)
- Supabase user JWTs: bearer tokens issued by Supabase Auth for end-user sessions
"""

from .models import CallerIdentity
from .api_key_auth import require_worldview_key
from .internal_auth import require_internal_token
from .supabase_auth import optional_supabase_user, require_supabase_user

__all__ = [
    "CallerIdentity",
    "require_worldview_key",
    "require_internal_token",
    "require_supabase_user",
    "optional_supabase_user",
]
