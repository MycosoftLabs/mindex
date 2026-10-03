"""Ledger integration with the shared retention.v1 identity and artifact authority.

No JWT parser, membership table, private blob store, or network client lives here.
The shared brief09 package must be integrated; its absence is a closed gate.
Operator/key policies are server-owned injected services, never request fields.
"""
from __future__ import annotations

import asyncio
import hashlib
from importlib import import_module
from typing import Any

from fastapi import HTTPException, Request


def retention_module():
    try:
        return import_module("mindex_api.routers.retention")
    except ImportError as exc:
        raise HTTPException(503, "shared_retention_contract_unavailable") from exc


async def require_ledger_principal(request: Request):
    """Use original JWT and freshly resolved tenant/project membership from09."""
    module = retention_module()
    async with asyncio.timeout(10):
        return await module.require_principal(request)


async def authorized_artifact_hashes(request: Request, principal: Any,
                                     artifact_ids: list[str]) -> dict[str, str]:
    service = retention_module().get_retention_service(request)
    result: dict[str, str] = {}
    async with asyncio.timeout(40):
        for artifact_id in artifact_ids:
            receipt, content = await service.content(principal, str(artifact_id))
            digest = hashlib.sha256(content).hexdigest()
            if receipt.get("sha256") != digest:
                raise HTTPException(409, "retained_artifact_hash_mismatch")
            result[str(artifact_id)] = digest
    # Revoke membership during remote object I/O and this operation fails closed.
    await service.repository.require_membership(principal)
    current = await require_ledger_principal(request)
    if any(getattr(current, field) != getattr(principal, field)
           for field in ("issuer", "subject", "tenant_id", "project_id")):
        raise HTTPException(403, "principal_changed_during_read")
    return result


async def trusted_source_key(request: Request, principal: Any, key_id: str):
    resolver = getattr(request.app.state, "ledger_source_key_resolver", None)
    if resolver is None:
        raise HTTPException(503, "trusted_source_key_authority_unavailable")
    async with asyncio.timeout(5):
        key = await resolver(principal, key_id)
    if key is None:
        raise HTTPException(403, "source_key_not_authorized")
    return key


async def require_operator(request: Request, principal: Any, policy_version: str | None):
    """Separate deployment-injected operator authority; customer != operator.

    Resolver must verify current grants for the exact four-part scope and return
    the allowed policy version. Any absent authority or nonmatching policy denies.
    A service API key or an unsigned forwarded role never reaches this interface.
    """
    resolver = getattr(request.app.state, "ledger_operator_resolver", None)
    if resolver is None:
        raise HTTPException(503, "ledger_operator_authority_unavailable")
    async with asyncio.timeout(5):
        grant = await resolver(principal)
    if (not isinstance(grant, dict) or grant.get("allowed") is not True
            or not isinstance(grant.get("policy_version"), str)
            or not grant["policy_version"]
            or policy_version is not None and grant["policy_version"] != policy_version):
        raise HTTPException(403, "ledger_operator_not_authorized")
    return grant
