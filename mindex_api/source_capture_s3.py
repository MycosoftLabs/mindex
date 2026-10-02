"""Injected private S3 adapter; imports never resolve AWS credentials or call AWS."""
from __future__ import annotations

import base64
import hashlib
import re

from .source_capture import CaptureConfig, CaptureError


class CaptureObjectStore:
    def __init__(self, client, config: CaptureConfig):
        self.client, self.config = client, config

    def validate(self):
        cfg = self.config
        if not (cfg.bucket and re.fullmatch(r"[a-zA-Z0-9/_-]+", cfg.prefix)
                and not cfg.prefix.startswith("/") and not cfg.prefix.endswith("/")
                and cfg.kms_key.startswith("arn:aws:kms:") and ":key/" in cfg.kms_key
                and re.fullmatch(r"[0-9]{12}", cfg.expected_owner) and cfg.region):
            raise CaptureError("archive_configuration_invalid")

    def _args(self, key):
        return {"Bucket": self.config.bucket, "Key": key,
                "ExpectedBucketOwner": self.config.expected_owner}

    def archive(self, row):
        self.validate()
        payload = bytes(row["payload"])
        if (len(payload) != row["byte_length"] or
                hashlib.sha256(payload).hexdigest() != row["sha256"]):
            raise CaptureError("capture_integrity_failed")
        key = f"{self.config.prefix}/{row['capture_id']}/{row['sha256']}"
        args = self._args(key)
        versioning = self.client.get_bucket_versioning(
            Bucket=self.config.bucket, ExpectedBucketOwner=self.config.expected_owner)
        if versioning.get("Status") != "Enabled":
            raise CaptureError("archive_versioning_required")
        try:
            uploaded = self.client.put_object(
                **args, Body=payload, ContentType="application/octet-stream",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(row["sha256"])).decode(),
                ServerSideEncryption="aws:kms", SSEKMSKeyId=self.config.kms_key,
                Metadata={"capture-id": str(row["capture_id"]), "sha256": row["sha256"]},
                IfNoneMatch="*",
            )
            version = uploaded.get("VersionId")
        except Exception as exc:
            # 412 means a prior attempt may have succeeded. It is not proof of
            # identity or integrity; verify the existing exact version below.
            response = getattr(exc, "response", {})
            error = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            if error not in {"PreconditionFailed", "412"}:
                raise CaptureError("archive_unavailable") from exc
            version = self.client.head_object(**args).get("VersionId")
        if not isinstance(version, str) or not version or version == "null":
            raise CaptureError("archive_versioning_required")
        reference = {"bucket": self.config.bucket, "key": key, "version": version}
        self.read(row, reference)
        return reference

    def read(self, row, reference=None):
        self.validate()
        reference = reference or {"bucket": row["object_bucket"], "key": row["object_key"],
                                  "version": row["object_version"]}
        # Never follow a DB reference to another account/bucket/prefix.
        expected_key = f"{self.config.prefix}/{row['capture_id']}/{row['sha256']}"
        if (reference["bucket"] != self.config.bucket or reference["key"] != expected_key or
                not reference["version"] or reference["version"] == "null"):
            raise CaptureError("capture_integrity_failed")
        response = self.client.get_object(**self._args(reference["key"]),
                                          VersionId=reference["version"], ChecksumMode="ENABLED")
        body = response.get("Body")
        try:
            if (body is None or response.get("VersionId") != reference["version"] or
                    response.get("ContentLength") != row["byte_length"] or
                    row["byte_length"] > self.config.max_payload_bytes or
                    response.get("ServerSideEncryption") != "aws:kms" or
                    response.get("SSEKMSKeyId") != self.config.kms_key or
                    response.get("Metadata", {}).get("capture-id") != str(row["capture_id"])):
                raise CaptureError("capture_integrity_failed")
            payload = body.read(self.config.max_payload_bytes + 1)
            if (len(payload) != row["byte_length"] or
                    hashlib.sha256(payload).hexdigest() != row["sha256"]):
                raise CaptureError("capture_integrity_failed")
            return payload
        finally:
            if body is not None:
                body.close()


def create_object_store(config: CaptureConfig):
    # Called only by explicit worker/archive retrieval, never at import/startup.
    import boto3
    from botocore.config import Config

    store = CaptureObjectStore(None, config)
    store.validate()
    store.client = boto3.client("s3", region_name=config.region,
        config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}))
    return store
