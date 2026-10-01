"""Shared retention.v1 value types and bounded admission contract."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

CONTRACT_VERSION = "retention.v1"


class RetentionError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    tenant_id: str
    project_id: str


@dataclass(frozen=True)
class RetentionConfig:
    enabled: bool = False
    max_payload_bytes: int = 8 * 1024 * 1024
    max_pending_bytes: int = 256 * 1024 * 1024
    max_pending_count: int = 1000
    lease_seconds: int = 120
    retention_days: int = 30
    bucket: str = ""
    prefix: str = "private-retention-v1"
    kms_key: str = ""
    expected_owner: str = ""
    region: str = ""

    def admission(self) -> None:
        if not self.enabled:
            raise RetentionError("retention_unavailable")
        if not (0 < self.max_payload_bytes <= 16 * 1024 * 1024
                and self.max_payload_bytes <= self.max_pending_bytes <= 1024 * 1024 * 1024
                and 0 < self.max_pending_count <= 100000
                and 30 <= self.lease_seconds <= 900 and 1 <= self.retention_days <= 3650):
            raise RetentionError("retention_configuration_invalid")

    @classmethod
    def from_env(cls) -> "RetentionConfig":
        values: dict[str, Any] = {}
        for name, field in cls.__dataclass_fields__.items():
            value = os.environ.get("RETENTION_" + name.upper())
            if value is not None:
                if name == "enabled":
                    values[name] = value.lower() == "true"
                elif isinstance(field.default, int):
                    try:
                        values[name] = int(value)
                    except ValueError as exc:
                        raise RetentionError("retention_configuration_invalid") from exc
                else:
                    values[name] = value
        return cls(**values)


def canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError("canonical UUID required")
        return str(parsed)
    except (ValueError, TypeError, AttributeError) as exc:
        raise RetentionError("invalid_identifier", 422) from exc


def admission_metadata(kind: str, idempotency_key: str, media_type: str,
                       source_event_at: str | None, config: RetentionConfig) -> dict[str, Any]:
    config.admission()
    if kind not in {"dataset", "chart", "artifact"}:
        raise RetentionError("invalid_artifact_kind", 422)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", idempotency_key):
        raise RetentionError("invalid_idempotency_key", 422)
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/geo+json", "text/csv",
                          "text/plain", "application/octet-stream", "image/svg+xml"}:
        raise RetentionError("unsupported_media_type", 415)
    observed = None
    if source_event_at is not None:
        try:
            observed = datetime.fromisoformat(source_event_at.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("timezone required")
            observed = observed.astimezone(timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise RetentionError("invalid_source_event_at", 422) from exc
    metadata = {"kind": kind, "idempotency_key": idempotency_key,
                "media_type": media_type, "source_event_at": observed}
    canonical = json.dumps({**metadata, "source_event_at": observed.isoformat() if observed else None},
                           sort_keys=True, separators=(",", ":"))
    metadata["metadata_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return metadata


def public_receipt(row: dict[str, Any]) -> dict[str, Any]:
    """Safe client projection; never expose storage coordinates, leases or raw bytes."""
    keys = ("artifact_id", "job_id", "kind", "sha256", "byte_length", "media_type", "state",
            "source_event_at", "received_at", "available_at", "retention_until",
            "tenant_id", "project_id", "last_error_code")
    result = {key: row.get(key) for key in keys}
    for key in ("artifact_id", "job_id", "tenant_id", "project_id"):
        if result[key] is not None:
            result[key] = str(result[key])
    verified = row.get("state") in {"verified", "archived_verified"}
    result.update(contract_version=CONTRACT_VERSION, classification="private",
                  durability="postgres_committed", archive_verified=verified,
                  task_id=result["job_id"], learned=False)
    return result
