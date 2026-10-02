"""Private service capture repository. No schema creation or import-time I/O."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text


class CaptureError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool = False
    sources: tuple[str, ...] = ()
    max_payload_bytes: int = 8 * 1024 * 1024
    max_pending_bytes: int = 256 * 1024 * 1024
    max_pending_count: int = 1000
    lease_seconds: int = 120
    bucket: str = ""
    prefix: str = "source-captures"
    kms_key: str = ""
    expected_owner: str = ""
    region: str = ""

    @classmethod
    def from_settings(cls, settings: Any) -> "CaptureConfig":
        return cls(
            enabled=settings.source_capture_enabled,
            sources=tuple(s.strip() for s in settings.source_capture_sources.split(",") if s.strip()),
            max_payload_bytes=settings.source_capture_max_payload_bytes,
            max_pending_bytes=settings.source_capture_max_pending_bytes,
            max_pending_count=settings.source_capture_max_pending_count,
            lease_seconds=settings.source_capture_lease_seconds,
            bucket=settings.source_capture_bucket or "",
            prefix=settings.source_capture_prefix,
            kms_key=settings.source_capture_kms_key or "",
            expected_owner=settings.source_capture_expected_owner or "",
            region=settings.source_capture_region or "",
        )

    def admission(self) -> None:
        if not self.enabled or not self.sources:
            raise CaptureError("capture_unavailable")
        if not (0 < self.max_payload_bytes <= 16 * 1024 * 1024
                and self.max_payload_bytes <= self.max_pending_bytes <= 1024 * 1024 * 1024
                and 0 < self.max_pending_count <= 100000 and 30 <= self.lease_seconds <= 900):
            raise CaptureError("capture_configuration_invalid")


def capture_metadata(source_id: str, idempotency_key: str, media_type: str,
                     content_encoding: str, observed_at: str | None,
                     config: CaptureConfig) -> dict[str, Any]:
    config.admission()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", source_id) or source_id not in config.sources:
        raise CaptureError("source_not_allowed", 422)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", idempotency_key):
        raise CaptureError("invalid_idempotency_key", 422)
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/geo+json", "text/csv",
                          "text/plain", "application/octet-stream"}:
        raise CaptureError("unsupported_media_type", 415)
    if content_encoding not in {"identity", "gzip"}:
        raise CaptureError("unsupported_content_encoding", 415)
    observed = None
    if observed_at:
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("timezone required")
            observed = observed.astimezone(timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise CaptureError("invalid_observed_at", 422) from exc
    metadata = {"source_id": source_id, "idempotency_key": idempotency_key,
                "media_type": media_type, "content_encoding": content_encoding,
                "observed_at": observed}
    fingerprint = json.dumps({**metadata, "observed_at": observed.isoformat() if observed else None},
                             sort_keys=True, separators=(",", ":"))
    metadata["metadata_sha256"] = hashlib.sha256(fingerprint.encode()).hexdigest()
    return metadata


def receipt(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("capture_id", "source_id", "sha256", "byte_length", "media_type", "content_encoding",
            "observed_at", "captured_at", "state", "attempt_count", "next_attempt_at",
            "last_error_code", "verified_at")
    result = {key: row.get(key) for key in keys}
    result["capture_id"] = str(result["capture_id"])
    result.update(classification="public_source", durability="postgres_committed",
                  cloud_verified=row["state"] == "archived_verified",
                  provenance="collector_reported_unverified")
    return result


class CaptureRepository:
    def __init__(self, session_factory, config: CaptureConfig):
        self.sessions, self.config = session_factory, config

    async def accept(self, service: str, metadata: dict[str, Any], payload: bytes):
        self.config.admission()
        if not service or len(service) > 128:
            raise CaptureError("invalid_service_identity", 403)
        if not payload or len(payload) > self.config.max_payload_bytes:
            raise CaptureError("payload_size_invalid", 413)
        digest = hashlib.sha256(payload).hexdigest()
        async with self.sessions() as db:
            try:
                # Serialize admissions (including duplicates) so aggregate capacity and
                # unique-key checks hold even across concurrent API workers.
                await db.execute(text("SELECT pg_advisory_xact_lock(1835626084, 1)"))
                params = {**metadata, "service": service}
                existing = (await db.execute(text("""SELECT * FROM raw_source.capture
                    WHERE service=:service AND source_id=:source_id
                    AND idempotency_key=:idempotency_key"""), params)).mappings().first()
                if existing:
                    if existing["sha256"] != digest or existing["metadata_sha256"] != metadata["metadata_sha256"]:
                        raise CaptureError("idempotency_conflict", 409)
                    await db.commit()
                    return receipt(dict(existing)), False
                pending = (await db.execute(text("""SELECT count(*) AS count,
                    COALESCE(sum(byte_length), 0) AS bytes FROM raw_source.capture
                    WHERE payload IS NOT NULL"""))).mappings().one()
                if (pending["count"] >= self.config.max_pending_count or
                        pending["bytes"] + len(payload) > self.config.max_pending_bytes):
                    raise CaptureError("capture_capacity_exhausted", 503)
                params.update(capture_id=str(uuid.uuid4()), sha256=digest,
                              byte_length=len(payload), payload=payload)
                row = (await db.execute(text("""INSERT INTO raw_source.capture
                    (capture_id, service, source_id, idempotency_key, metadata_sha256,
                     sha256, byte_length, media_type, content_encoding, observed_at, payload)
                    VALUES (CAST(:capture_id AS UUID), :service, :source_id, :idempotency_key,
                     :metadata_sha256, :sha256, :byte_length, :media_type, :content_encoding,
                     :observed_at, :payload) RETURNING *"""), params)).mappings().one()
                await db.commit()  # Never acknowledge before this succeeds.
                return receipt(dict(row)), True
            except CaptureError:
                await db.rollback()
                raise
            except Exception as exc:
                await db.rollback()
                raise CaptureError("capture_storage_unavailable") from exc

    async def get(self, capture_id: str) -> dict[str, Any]:
        async with self.sessions() as db:
            try:
                row = (await db.execute(text("SELECT * FROM raw_source.capture WHERE capture_id=CAST(:id AS UUID)"),
                                        {"id": capture_id})).mappings().first()
            except Exception as exc:
                raise CaptureError("capture_storage_unavailable") from exc
            if row is None:
                raise CaptureError("capture_not_found", 404)
            return dict(row)

    async def claim(self) -> dict[str, Any] | None:
        async with self.sessions() as db:
            try:
                row = (await db.execute(text("""SELECT * FROM raw_source.capture
                    WHERE (state='pending_archive' AND next_attempt_at <= clock_timestamp())
                       OR (state='archiving' AND lease_expires_at <= clock_timestamp())
                    ORDER BY captured_at, capture_id FOR UPDATE SKIP LOCKED LIMIT 1"""))).mappings().first()
                if row is None:
                    await db.rollback()
                    return None
                token = str(uuid.uuid4())
                result = (await db.execute(text("""UPDATE raw_source.capture SET state='archiving',
                    lease_token=CAST(:token AS UUID),
                    lease_expires_at=clock_timestamp() + :seconds * interval '1 second',
                    attempt_count=attempt_count+1
                    WHERE capture_id=:id RETURNING *"""),
                    {"id": row["capture_id"], "token": token,
                     "seconds": self.config.lease_seconds})).mappings().one()
                await db.commit()
                return dict(result)
            except Exception as exc:
                await db.rollback()
                raise CaptureError("capture_storage_unavailable") from exc

    async def complete(self, row: dict[str, Any], obj: dict[str, str]) -> bool:
        return await self._transition(row, """state='archived_verified', payload=NULL,
            object_bucket=:bucket, object_key=:key, object_version=:version,
            verified_at=clock_timestamp(), last_error_code=NULL,
            lease_token=NULL, lease_expires_at=NULL""", obj)

    async def retry(self, row: dict[str, Any], code: str, integrity: bool = False) -> bool:
        delay = min(3600, 2 ** min(int(row["attempt_count"]), 11))
        return await self._transition(row, """state=:state, last_error_code=:error,
            next_attempt_at=clock_timestamp() + :delay * interval '1 second',
            lease_token=NULL, lease_expires_at=NULL""",
            {"state": "integrity_blocked" if integrity else "pending_archive",
             "error": code, "delay": delay})

    async def _transition(self, row, assignment, params):
        # assignment is a fixed internal SQL fragment, never caller input.
        async with self.sessions() as db:
            try:
                result = await db.execute(text("UPDATE raw_source.capture SET " + assignment + """
                    WHERE capture_id=:id AND lease_token=:token AND state='archiving'
                    AND lease_expires_at > clock_timestamp() RETURNING capture_id"""),
                    {**params, "id": row["capture_id"], "token": row["lease_token"]})
                changed = result.first() is not None
                await db.commit()
                return changed
            except Exception as exc:
                await db.rollback()
                raise CaptureError("capture_storage_unavailable") from exc


async def archive_one(repository: CaptureRepository, object_store) -> bool:
    """At-least-once work with immutable object identity and fenced completion."""
    import asyncio

    row = await repository.claim()
    if row is None:
        return False
    try:
        obj = await asyncio.to_thread(object_store.archive, row)
    except CaptureError as exc:
        await repository.retry(row, exc.code, integrity=exc.code == "capture_integrity_failed")
    except Exception:
        await repository.retry(row, "archive_unavailable")
    else:
        await repository.complete(row, obj)
    return True
