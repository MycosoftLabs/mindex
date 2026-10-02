"""Private, version-pinned S3 retention. No credentials or I/O resolve at import.

Authorization belongs to the MINDEX service: this adapter accepts persisted rows
only, never browser supplied references, and never produces presigned URLs.
"""
from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from uuid import UUID

from .contracts import RetentionConfig, RetentionError


_PUBLIC_ACCESS_FLAGS = (
    "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"
)
_MISSING = {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}
_RETRYABLE = {
    "412", "PreconditionFailed", "409", "ConditionalRequestConflict", "RequestTimeout",
    "RequestTimeoutException", "InternalError", "InternalFailure", "ServiceUnavailable",
    "SlowDown", "500", "502", "503", "504",
}


def _error_code(exc):
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error", {})
        if isinstance(error, Mapping):
            return str(error.get("Code", ""))
    return ""


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RetentionError("archive_retention_invalid")
    return value.astimezone(timezone.utc)


class PrivateObjectStore:
    """Bounded single-object storage; callers must enforce membership per operation."""

    def __init__(self, client, config: RetentionConfig):
        self.client, self.config = client, config

    def validate(self):
        cfg = self.config
        cfg.admission()
        if not (
            isinstance(cfg.bucket, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", cfg.bucket)
            and isinstance(cfg.prefix, str)
            and re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", cfg.prefix)
            and isinstance(cfg.expected_owner, str)
            and re.fullmatch(r"[0-9]{12}", cfg.expected_owner)
            and isinstance(cfg.region, str)
            and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", cfg.region)
            and isinstance(cfg.kms_key, str)
            and re.fullmatch(
                rf"arn:aws(?:-us-gov|-cn)?:kms:{re.escape(cfg.region)}:"
                rf"{re.escape(cfg.expected_owner)}:key/[a-zA-Z0-9-]+", cfg.kms_key
            )
        ):
            raise RetentionError("archive_configuration_invalid")

    def _bucket_args(self):
        return {"Bucket": self.config.bucket, "ExpectedBucketOwner": self.config.expected_owner}

    def _args(self, key, version=None):
        args = {**self._bucket_args(), "Key": key}
        if version is not None:
            args["VersionId"] = version
        return args

    def _call(self, operation, **kwargs):
        try:
            response = getattr(self.client, operation)(**kwargs)
        except Exception:
            # SDK exceptions may contain source content, keys, or service details.
            raise RetentionError("archive_unavailable") from None
        if not isinstance(response, Mapping):
            raise RetentionError("archive_integrity_failed")
        return response

    def _policy(self):
        """Fail closed on a missing policy or permission; never configure AWS here."""
        args = self._bucket_args()
        if self._call("get_bucket_versioning", **args).get("Status") != "Enabled":
            raise RetentionError("archive_versioning_required")
        block = self._call("get_public_access_block", **args).get("PublicAccessBlockConfiguration", {})
        if not isinstance(block, Mapping) or any(block.get(flag) is not True for flag in _PUBLIC_ACCESS_FLAGS):
            raise RetentionError("archive_private_bucket_required")
        ownership = self._call("get_bucket_ownership_controls", **args).get("OwnershipControls", {})
        if not isinstance(ownership, Mapping) or ownership.get("Rules") != [{"ObjectOwnership": "BucketOwnerEnforced"}]:
            raise RetentionError("archive_owner_enforced_required")
        lock = self._call("get_object_lock_configuration", **args).get("ObjectLockConfiguration", {})
        if not isinstance(lock, Mapping) or lock.get("ObjectLockEnabled") != "Enabled":
            raise RetentionError("archive_object_lock_required")

    def _row(self, row):
        self.validate()
        try:
            if row["classification"] != "private":
                raise ValueError
            # UUID parsing prevents identity/subject strings and path injection.
            ids = [str(UUID(str(row[name]))) for name in ("tenant_id", "project_id", "artifact_id")]
            digest, length = row["sha256"], row["byte_length"]
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or type(length) is not int or not 0 <= length <= self.config.max_payload_bytes):
                raise ValueError
            retained_until = _utc(row["retention_until"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise RetentionError("archive_integrity_failed") from None
        return f"{self.config.prefix}/{'/'.join(ids)}", digest, length, retained_until, ids[-1]

    @staticmethod
    def _version(version):
        if not isinstance(version, str) or not version or version == "null" or len(version) > 1024:
            raise RetentionError("archive_versioning_required")
        return version

    def _reference(self, row, reference):
        key, digest, length, retained_until, artifact_id = self._row(row)
        try:
            if reference is None:
                reference = {"bucket": row["object_bucket"], "key": row["object_key"],
                             "version": row["object_version"]}
            if reference["bucket"] != self.config.bucket or reference["key"] != key:
                raise RetentionError("archive_integrity_failed")
            version = self._version(reference["version"])
        except (KeyError, TypeError):
            raise RetentionError("archive_integrity_failed") from None
        return key, version, digest, length, retained_until, artifact_id

    def _headers(self, response, version, digest, length, retained_until, artifact_id):
        metadata = response.get("Metadata", {})
        expected_checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if (
            response.get("VersionId") != version or response.get("DeleteMarker") is True
            or response.get("ContentLength") != length
            or response.get("ServerSideEncryption") != "aws:kms"
            or response.get("SSEKMSKeyId") != self.config.kms_key
            or metadata != {"artifact-id": artifact_id, "sha256": digest}
            or response.get("ChecksumSHA256") != expected_checksum
        ):
            raise RetentionError("archive_integrity_failed")
        if (response.get("ObjectLockMode") != "COMPLIANCE"
                or _utc(response.get("ObjectLockRetainUntilDate")) < retained_until):
            raise RetentionError("archive_retention_unverified")

    def _head_or_missing(self, key, version=None):
        try:
            response = self.client.head_object(**self._args(key, version), ChecksumMode="ENABLED")
        except Exception as exc:
            if _error_code(exc) in _MISSING:
                return None
            raise RetentionError("archive_unavailable") from None
        if not isinstance(response, Mapping):
            raise RetentionError("archive_integrity_failed")
        return response

    def archive(self, row):
        key, digest, length, retained_until, _ = self._row(row)
        if retained_until <= datetime.now(timezone.utc):
            raise RetentionError("archive_retention_expired")
        try:
            source = row["payload"]
            if not isinstance(source, (bytes, bytearray, memoryview)) or len(source) > self.config.max_payload_bytes:
                raise ValueError
            payload = bytes(source)
            if len(payload) != length or hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RetentionError("archive_integrity_failed") from None
        self._policy()
        # S3 timestamps have second precision; round upwards so verification
        # never shortens PostgreSQL's potentially fractional retention deadline.
        upload_until = retained_until
        if retained_until.microsecond:
            upload_until = (retained_until + timedelta(seconds=1)).replace(microsecond=0)
        upload_args = {
            **self._args(key), "Body": payload, "ContentType": "application/octet-stream",
            "ContentLength": length, "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
            "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.config.kms_key,
            "Metadata": {"artifact-id": str(UUID(str(row["artifact_id"]))), "sha256": digest},
            "IfNoneMatch": "*", "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": upload_until,
        }
        # An ambiguous timeout may have committed. Resolve that immutable key;
        # only a confirmed missing object permits one conditional retry.
        version = None
        for attempt in range(2):
            try:
                uploaded = self.client.put_object(**upload_args)
            except Exception as exc:
                code = _error_code(exc)
                if code and code not in _RETRYABLE:
                    raise RetentionError("archive_unavailable") from None
                found = self._head_or_missing(key)
                if found is not None:
                    version = self._version(found.get("VersionId"))
                    break
                if attempt == 1:
                    raise RetentionError("archive_unavailable") from None
            else:
                if not isinstance(uploaded, Mapping):
                    raise RetentionError("archive_integrity_failed")
                version = self._version(uploaded.get("VersionId"))
                break
        reference = {"bucket": self.config.bucket, "key": key, "version": version}
        self.read(row, reference)
        return {**reference, "verified": True, "sha256": digest, "byte_length": length}

    def read(self, row, reference=None):
        key, version, digest, length, retained_until, artifact_id = self._reference(row, reference)
        self._policy()
        response = self._call("get_object", **self._args(key, version), ChecksumMode="ENABLED")
        body = response.get("Body")
        try:
            self._headers(response, version, digest, length, retained_until, artifact_id)
            if body is None:
                raise RetentionError("archive_integrity_failed")
            # Read at most the declared length plus one, not an unbounded body.
            # A conforming StreamingBody may return short chunks: read to EOF.
            chunks, total = [], 0
            while total <= length:
                chunk = body.read(min(64 * 1024, length + 1 - total))
                if not isinstance(chunk, bytes):
                    raise RetentionError("archive_integrity_failed")
                if not chunk:
                    break
                total += len(chunk)
                if total > length:
                    raise RetentionError("archive_integrity_failed")
                chunks.append(chunk)
            payload = b"".join(chunks)
            if total != length or hashlib.sha256(payload).hexdigest() != digest:
                raise RetentionError("archive_integrity_failed")
            return payload
        except RetentionError:
            raise
        except Exception:
            raise RetentionError("archive_unavailable") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    # Closing after read never exposes raw SDK errors.
                    pass

    def delete(self, row):
        """Physical purge after expiry. MINDEX revocation precedes this operation."""
        key, version, digest, length, retained_until, artifact_id = self._reference(row, None)
        now = datetime.now(timezone.utc)
        if retained_until > now:
            raise RetentionError("archive_retention_active", status=409)
        self._policy()
        existing = self._head_or_missing(key, version)
        if existing is not None:
            self._headers(existing, version, digest, length, retained_until, artifact_id)
            if (_utc(existing["ObjectLockRetainUntilDate"]) > now
                    or existing.get("ObjectLockLegalHoldStatus") == "ON"):
                raise RetentionError("archive_retention_active", status=409)
            try:
                self.client.delete_object(**self._args(key, version))
            except Exception as exc:
                if _error_code(exc) and _error_code(exc) not in _RETRYABLE:
                    raise RetentionError("archive_unavailable") from None
            # Success is absence of the exact version, never a delete marker.
            if self._head_or_missing(key, version) is not None:
                raise RetentionError("archive_deletion_unverified")
        return {"bucket": self.config.bucket, "key": key, "version": version, "deleted": True}

    def reconcile_delete(self, row):
        """Reconcile a tombstoned row whose archive version never reached Postgres.

        The key is derived exclusively from canonical tenant/project/artifact UUIDs
        in the persisted row. This performs no bucket/version listing and never
        accepts an object coordinate supplied by a caller.
        """
        if row.get("object_version") is not None:
            raise RetentionError("archive_integrity_failed")
        key, digest, length, retained_until, artifact_id = self._row(row)
        if retained_until > datetime.now(timezone.utc):
            raise RetentionError("archive_retention_active", status=409)
        self._policy()
        existing = self._head_or_missing(key)
        if existing is None:
            return {"bucket": self.config.bucket, "key": key, "version": None,
                    "deleted": True, "reconciled": True, "absent": True}
        version = self._version(existing.get("VersionId"))
        self._headers(existing, version, digest, length, retained_until, artifact_id)
        reference = {"bucket": self.config.bucket, "key": key, "version": version}
        # Validate complete immutable readback before the adapter removes anything.
        self.read(row, reference)
        now = datetime.now(timezone.utc)
        if (_utc(existing["ObjectLockRetainUntilDate"]) > now
                or existing.get("ObjectLockLegalHoldStatus") == "ON"):
            raise RetentionError("archive_retention_active", status=409)
        try:
            self.client.delete_object(**self._args(key, version))
        except Exception as exc:
            if _error_code(exc) and _error_code(exc) not in _RETRYABLE:
                raise RetentionError("archive_unavailable") from None
        if self._head_or_missing(key, version) is not None:
            raise RetentionError("archive_deletion_unverified")
        return {**reference, "deleted": True, "reconciled": True, "absent": False}


def create_object_store(config: RetentionConfig):
    """Explicit lazy factory; no import-time AWS calls or custom endpoint bypass."""
    store = PrivateObjectStore(None, config)
    store.validate()
    try:
        import boto3
        from botocore.config import Config

        store.client = boto3.client(
            "s3", region_name=config.region,
            config=Config(connect_timeout=5, read_timeout=15, max_pool_connections=4,
                          ignore_configured_endpoint_urls=True,
                          retries={"mode": "standard", "total_max_attempts": 2}),
        )
    except Exception:
        raise RetentionError("archive_client_unavailable") from None
    return store
