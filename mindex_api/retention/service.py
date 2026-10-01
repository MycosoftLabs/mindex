"""Application boundary: exact archive readback before memory and download."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Any

from .contracts import Principal, RetentionConfig, RetentionError, canonical_uuid, public_receipt


class RetentionService:
    def __init__(self, repository, verifier, object_store, config: RetentionConfig):
        self.repository, self.verifier = repository, verifier
        self.object_store, self.config = object_store, config

    async def admit(self, principal: Principal, metadata: dict, payload: bytes):
        """Internal app worker seam. Principal must come from its committed job.

        Repository admission rechecks live membership inside the transaction.
        Browser requests always enter through the JWT dependency first.
        """
        return await self.repository.admit(principal, metadata, payload)

    async def authenticate(self, authorization: str, tenant_id: str, project_id: str) -> Principal:
        self.config.admission()
        if not authorization.startswith("Bearer ") or len(authorization) > 17000:
            raise RetentionError("authentication_required", 401)
        principal = await self.verifier.verify(authorization[7:], tenant_id, project_id)
        await self.repository.require_membership(principal)
        return principal

    async def content(self, principal: Principal, artifact_id: str) -> tuple[dict, bytes]:
        artifact_id = canonical_uuid(artifact_id)
        row = await self.repository.get(principal, artifact_id)
        if row["state"] not in {"verified", "archived_verified"}:
            raise RetentionError("artifact_not_verified", 409)
        try:
            async with asyncio.timeout(30):
                payload = await asyncio.to_thread(self.object_store.read, row)
        except RetentionError:
            raise
        except TimeoutError as exc:
            raise RetentionError("archive_timeout", 504) from exc
        except Exception as exc:
            raise RetentionError("archive_unavailable") from exc
        if (len(payload) != row["byte_length"] or len(payload) > self.config.max_payload_bytes
                or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), row["sha256"])):
            raise RetentionError("artifact_integrity_failed")
        # A revocation/delete during network I/O must not yield cached private bytes.
        current = await self.repository.get(principal, artifact_id)
        if (current["state"] not in {"verified", "archived_verified"}
                or current["sha256"] != row["sha256"]
                or current.get("object_version") != row.get("object_version")):
            raise RetentionError("artifact_unavailable", 409)
        return public_receipt(current), payload

    async def remember(self, principal: Principal, artifact_id: str, summary: str) -> dict[str, Any]:
        if not isinstance(summary, str) or not 1 <= len(summary) <= 2000:
            raise RetentionError("invalid_memory_summary", 422)
        receipt, payload = await self.content(principal, artifact_id)
        proof = hashlib.sha256(payload).hexdigest()
        linked = await self.repository.memory_link(principal, artifact_id, summary, proof_sha256=proof)
        readback = await self.repository.memory_get(principal, str(linked["memory_id"]))
        if any(readback.get(k) != linked.get(k) for k in ("memory_id", "artifact_id", "summary")):
            raise RetentionError("memory_readback_failed")
        # Exact MINDEX reference receipt. No assertion that a model was trained.
        return {**readback, "contract_version": "retention.v1", "reference_verified": True,
                "artifact_sha256": receipt["sha256"], "learned": False}

    async def recall(self, principal: Principal, memory_id: str) -> dict[str, Any]:
        memory = await self.repository.memory_get(principal, canonical_uuid(memory_id))
        receipt, _ = await self.content(principal, str(memory["artifact_id"]))
        current = await self.repository.memory_get(principal, memory_id)
        return {**current, "contract_version": "retention.v1", "reference_verified": True,
                "artifact_sha256": receipt["sha256"], "learned": False}


async def archive_one(repository, object_store) -> bool:
    """One recoverable outbox attempt. The database owns the lease and fence."""
    row = await repository.claim()
    if row is None:
        return False
    try:
        async with asyncio.timeout(40):
            reference = await asyncio.to_thread(object_store.archive, row)
        if not await repository.complete(row, reference):
            await repository.register_orphan(row, reference)
    except RetentionError as exc:
        await repository.retry(row, exc.code, integrity=any(
            word in exc.code for word in ("integrity", "mismatch", "quarantine", "retention_unverified", "retention_invalid")))
    except (TimeoutError, OSError):
        await repository.retry(row, "archive_unavailable")
    return True


async def purge_one(repository, object_store) -> bool:
    row = await repository.claim_purge()
    orphan = False
    if row is None:
        row = await repository.claim_orphan_purge()
        orphan = True
        if row is None:
            return False
    try:
        async with asyncio.timeout(40):
            proof = await asyncio.to_thread(object_store.delete, row)
        if orphan:
            await repository.complete_orphan_purge(row, proof)
        else:
            await repository.complete_purge(row, proof)
    except (RetentionError, TimeoutError, OSError):
        # Keep leased deletion durable and retry after expiry; never claim erased.
        pass
    return True
