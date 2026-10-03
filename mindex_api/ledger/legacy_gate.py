"""Compatibility switch for the legacy unscoped ledger, integrity and IP asset routes.

The legacy routes stay fully functional by default because live website pages
depend on them. Setting LEDGER_LEGACY_ROUTES_ENABLED=false retires them with a
503 that points callers at the tenant-scoped provenance API.
"""
from __future__ import annotations

import os

from fastapi import HTTPException

LEGACY_TENANT_MIGRATION_REQUIRED = "legacy_tenant_migration_required"
_DISABLED_VALUES = {"0", "false", "no", "off"}


def legacy_routes_enabled() -> bool:
    raw = os.getenv("LEDGER_LEGACY_ROUTES_ENABLED", "true")
    return raw.strip().lower() not in _DISABLED_VALUES


def require_legacy_routes_enabled() -> None:
    if legacy_routes_enabled():
        return
    raise HTTPException(
        status_code=503,
        detail={
            "code": LEGACY_TENANT_MIGRATION_REQUIRED,
            "message": (
                "Legacy ledger routes are retired on this deployment; "
                "use the tenant-scoped provenance API at /ledger/provenance/v1."
            ),
        },
    )
