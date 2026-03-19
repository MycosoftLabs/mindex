"""Caller identity models for auth context propagation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List


@dataclass
class CallerIdentity:
    """Identity of the authenticated caller, available to all request handlers."""

    key_id: uuid.UUID
    owner_id: uuid.UUID
    user_type: str          # 'human' | 'agent' | 'service'
    plan: str               # 'free' | 'pro' | 'enterprise' | 'internal'
    scopes: List[str] = field(default_factory=list)
    rate_limit_per_minute: int = 60
    rate_limit_per_day: int = 10000
    service: str = "worldview"
