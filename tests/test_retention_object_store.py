"""Offline S3 error-injection tests, not AWS IAM/retention qualification."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import ModuleType

import pytest

from mindex_api.retention.contracts import RetentionConfig, RetentionError
from mindex_api.retention.object_store import PrivateObjectStore, create_object_store


class S3Error(Exception):
    def __init__(self, code):
        super().__init__("private-user@example.invalid PRIVATE SOURCE CONTENT")
        self.response = {"Error": {"Code": code}}


class TrackedBody(io.BytesIO):
    def __init__(self, data, chunk_size=None, fail=False):
        super().__init__(data)
        self.requests = []
        self.chunk_size = chunk_size
        self.fail = fail

    def read(self, amount=-1):
        self.requests.append(amount)
        assert 0 < amount <= 65536
        if self.fail:
            raise OSError("PRIVATE SOURCE CONTENT")
        return super().read(min(amount, self.chunk_size) if self.chunk_size else amount)


class FakeS3:
    """Small stateful fake with independent persisted versions and call failures."""

    def __init__(self):
        self.calls = []
        self.objects = {}
        self.latest = {}
        self.failures = {}
        self.upload_outcomes = []
        self.body = None
        self.payload_override = None
        self.read_headers = {}
        self.chunk_size = None
        self.fail_body = False
        self.delete_outcome = "delete"
        self.versioning = {"Status": "Enabled"}
        self.public_block = {"PublicAccessBlockConfiguration": dict.fromkeys(
            ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"), True)}
        self.ownership = {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}
        self.lock = {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def _record(self, operation, args):
        self.calls.append((operation, args))
        if operation in self.failures:
            raise self.failures[operation]

    def get_bucket_versioning(self, **args):
        self._record("get_bucket_versioning", args)
        return self.versioning

    def get_public_access_block(self, **args):
        self._record("get_public_access_block", args)
        return self.public_block

    def get_bucket_ownership_controls(self, **args):
        self._record("get_bucket_ownership_controls", args)
        return self.ownership

    def get_object_lock_configuration(self, **args):
        self._record("get_object_lock_configuration", args)
        return self.lock

    def put_object(self, **args):
        self._record("put_object", args)
        outcome = self.upload_outcomes.pop(0) if self.upload_outcomes else "success"
        if isinstance(outcome, Exception):
            raise outcome
        if args["Key"] in self.latest:
            raise S3Error("PreconditionFailed")
        version = f"version-{len(self.objects) + 1}"
        self.objects[(args["Key"], version)] = {
            "VersionId": version, "ContentLength": args["ContentLength"],
            "ServerSideEncryption": args["ServerSideEncryption"], "SSEKMSKeyId": args["SSEKMSKeyId"],
            "ChecksumSHA256": args["ChecksumSHA256"], "Metadata": copy.deepcopy(args["Metadata"]),
            "ObjectLockMode": args["ObjectLockMode"],
            "ObjectLockRetainUntilDate": args["ObjectLockRetainUntilDate"], "payload": args["Body"],
        }
        self.latest[args["Key"]] = version
        if outcome == "commit-timeout":
            raise TimeoutError("PRIVATE SOURCE CONTENT")
        return {"VersionId": version}

    def _object(self, args):
        version = args.get("VersionId", self.latest.get(args["Key"]))
        if (args["Key"], version) not in self.objects:
            raise S3Error("NoSuchVersion")
        return copy.deepcopy(self.objects[(args["Key"], version)])

    def head_object(self, **args):
        self._record("head_object", args)
        result = self._object(args)
        result.pop("payload")
        return result

    def get_object(self, **args):
        self._record("get_object", args)
        result = self._object(args)
        payload = result.pop("payload") if self.payload_override is None else self.payload_override
        self.body = TrackedBody(payload, self.chunk_size, self.fail_body)
        result.update(self.read_headers)
        result["Body"] = self.body
        return result

    def delete_object(self, **args):
        self._record("delete_object", args)
        if self.delete_outcome != "keep":
            self.objects.pop((args["Key"], args["VersionId"]), None)
        if self.delete_outcome == "commit-timeout":
            raise TimeoutError("PRIVATE SOURCE CONTENT")
        return {"VersionId": args["VersionId"]}


@pytest.fixture
def cfg():
    return RetentionConfig(enabled=True, bucket="private-test-bucket", prefix="private-retention-v1",
                           expected_owner="123456789012", region="us-west-2",
                           kms_key="arn:aws:kms:us-west-2:123456789012:key/test-key")


@pytest.fixture
def row():
    payload = b"private scientific dataset\n"
    return {
        "tenant_id": "2c05e220-8d2f-4c86-bf47-8b0cd12a8b23",
        "project_id": "5b84ee26-f338-4385-bd2a-c4a588be9cd8",
        "artifact_id": "e04c3048-cd2f-45e4-850c-9b4c5f5a4b44",
        "job_id": "2c62e0aa-6eb2-4b8b-94d5-2f4824a7fcaf",
        "subject": "private-user@example.invalid", "classification": "private",
        "payload": payload, "byte_length": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        "retention_until": datetime.now(timezone.utc) + timedelta(days=30),
    }


def archived(cfg, row):
    client = FakeS3()
    store = PrivateObjectStore(client, cfg)
    reference = store.archive(row)
    row.update(object_bucket=reference["bucket"], object_key=reference["key"], object_version=reference["version"])
    return store, client, reference


def expired(cfg, row):
    store, client, reference = archived(cfg, row)
    row["retention_until"] = datetime.now(timezone.utc) - timedelta(days=1)
    client.objects[(reference["key"], reference["version"])]["ObjectLockRetainUntilDate"] = row["retention_until"]
    return store, client, reference


def test_archive_roundtrip_requires_exact_version_owner_checksum_lock_and_private_metadata(cfg, row):
    store, client, ref = archived(cfg, row)
    assert ref["verified"] is True and ref["sha256"] == row["sha256"]
    assert ref["byte_length"] == row["byte_length"]
    assert ref["key"] == f"{cfg.prefix}/{row['tenant_id']}/{row['project_id']}/{row['artifact_id']}"
    assert store.read(row) == row["payload"]
    assert client.body.closed
    assert all(args["ExpectedBucketOwner"] == cfg.expected_owner for _, args in client.calls)
    put = next(args for op, args in client.calls if op == "put_object")
    assert put["IfNoneMatch"] == "*" and put["ChecksumAlgorithm"] == "SHA256"
    assert put["ObjectLockMode"] == "COMPLIANCE"
    assert put["ObjectLockRetainUntilDate"] >= row["retention_until"]
    assert put["ObjectLockRetainUntilDate"].microsecond == 0
    assert set(put["Metadata"]) == {"artifact-id", "sha256"}
    assert row["subject"] not in str(put["Metadata"]) + ref["key"]
    assert "ACL" not in put
    assert not hasattr(store, "generate_presigned_url")


@pytest.mark.parametrize("operation", ["get_bucket_versioning", "get_public_access_block",
    "get_bucket_ownership_controls", "get_object_lock_configuration", "put_object", "get_object"])
def test_permission_failures_are_sanitized_and_never_return_verified(cfg, row, operation, caplog):
    client = FakeS3()
    client.failures[operation] = S3Error("AccessDenied")
    with pytest.raises(RetentionError, match="archive_unavailable") as caught:
        PrivateObjectStore(client, cfg).archive(row)
    assert caught.value.__suppress_context__
    assert "PRIVATE SOURCE" not in str(caught.value) + caplog.text
    assert row["subject"] not in str(caught.value) + caplog.text


@pytest.mark.parametrize("flag", ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"])
def test_each_public_access_block_flag_is_required(cfg, row, flag):
    client = FakeS3()
    client.public_block["PublicAccessBlockConfiguration"][flag] = False
    with pytest.raises(RetentionError, match="archive_private_bucket_required"):
        PrivateObjectStore(client, cfg).archive(row)
    assert not any(op == "put_object" for op, _ in client.calls)


@pytest.mark.parametrize(("attribute", "value", "error"), [
    ("versioning", {"Status": "Suspended"}, "archive_versioning_required"),
    ("ownership", {"OwnershipControls": {"Rules": [{"ObjectOwnership": "ObjectWriter"}]}}, "archive_owner_enforced_required"),
    ("lock", {}, "archive_object_lock_required"),
])
def test_bucket_policy_prerequisites_fail_closed(cfg, row, attribute, value, error):
    client = FakeS3()
    setattr(client, attribute, value)
    with pytest.raises(RetentionError, match=error):
        PrivateObjectStore(client, cfg).archive(row)


@pytest.mark.parametrize(("field", "value"), [
    ("bucket", "arn:aws:s3:::elsewhere"), ("prefix", "../public"), ("prefix", "/private/"),
    ("expected_owner", "wrong"), ("kms_key", "alias/default"),
    ("kms_key", "arn:aws:kms:us-east-1:123456789012:key/other-region"),
    ("kms_key", "arn:aws:kms:us-west-2:999999999999:key/other-account"),
])
def test_invalid_configuration_never_calls_aws(cfg, row, field, value):
    client = FakeS3()
    with pytest.raises(RetentionError, match="archive_configuration_invalid"):
        PrivateObjectStore(client, replace(cfg, **{field: value})).archive(row)
    assert not client.calls


@pytest.mark.parametrize(("field", "value"), [
    ("classification", "public"), ("tenant_id", "private-user@example.invalid"),
    ("project_id", "../other"), ("artifact_id", "../other"), ("sha256", "bogus"),
    ("byte_length", 999), ("byte_length", True), ("payload", b"altered bytes"),
    ("payload", "not bytes"),
])
def test_invalid_persisted_row_and_payload_never_upload(cfg, row, field, value):
    client = FakeS3()
    row[field] = value
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        PrivateObjectStore(client, cfg).archive(row)
    assert not client.calls


@pytest.mark.parametrize("value", [None, "2030-01-01", datetime(2030, 1, 1)])
def test_retention_deadline_requires_aware_datetime(cfg, row, value):
    row["retention_until"] = value
    with pytest.raises(RetentionError, match="archive_retention_invalid"):
        PrivateObjectStore(FakeS3(), cfg).archive(row)


def test_expired_deadline_cannot_be_first_archived(cfg, row):
    row["retention_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    client = FakeS3()
    with pytest.raises(RetentionError, match="archive_retention_expired"):
        PrivateObjectStore(client, cfg).archive(row)
    assert not client.calls


@pytest.mark.parametrize(("field", "value"), [
    ("VersionId", "another-version"), ("ContentLength", 1), ("ServerSideEncryption", "AES256"),
    ("SSEKMSKeyId", "arn:aws:kms:us-west-2:123456789012:key/another-key"),
    ("Metadata", {}), ("ChecksumSHA256", "wrong"), ("DeleteMarker", True),
])
def test_readback_mismatch_closes_stream_and_never_verifies(cfg, row, field, value):
    client = FakeS3()
    client.read_headers[field] = value
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        PrivateObjectStore(client, cfg).archive(row)
    assert client.body.closed


@pytest.mark.parametrize("change", ["artifact-id", "sha256", "private-subject"])
def test_metadata_identity_digest_and_allowlist_all_verified(cfg, row, change):
    client = FakeS3()
    metadata = {"artifact-id": row["artifact_id"], "sha256": row["sha256"]}
    metadata[change] = "private-user@example.invalid"
    client.read_headers["Metadata"] = metadata
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        PrivateObjectStore(client, cfg).archive(row)
    assert client.body.closed


@pytest.mark.parametrize("change", ["governance", "shorter", "missing"])
def test_readback_retention_must_be_enforced_through_requested_date(cfg, row, change):
    client = FakeS3()
    if change == "governance":
        client.read_headers["ObjectLockMode"] = "GOVERNANCE"
    else:
        client.read_headers["ObjectLockRetainUntilDate"] = (None if change == "missing"
            else row["retention_until"] - timedelta(seconds=1))
    with pytest.raises(RetentionError, match="archive_retention_"):
        PrivateObjectStore(client, cfg).archive(row)
    assert client.body.closed


@pytest.mark.parametrize("payload", [b"short", b"x" * 26, b"x" * 30])
def test_truncation_hash_mismatch_and_overlong_body_are_rejected(cfg, row, payload):
    client = FakeS3()
    client.payload_override = payload
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        PrivateObjectStore(client, cfg).archive(row)
    assert client.body.closed
    assert max(client.body.requests) <= row["byte_length"] + 1


def test_short_chunks_are_joined_and_read_failure_closes_stream(cfg, row):
    client = FakeS3()
    client.chunk_size = 2
    store = PrivateObjectStore(client, cfg)
    ref = store.archive(row)
    assert store.read(row, ref) == row["payload"]
    client.fail_body = True
    with pytest.raises(RetentionError, match="archive_unavailable"):
        store.read(row, ref)
    assert client.body.closed


def test_maximum_payload_limit_checked_before_io(cfg, row):
    client = FakeS3()
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        PrivateObjectStore(client, replace(cfg, max_payload_bytes=4)).archive(row)
    assert not client.calls


@pytest.mark.parametrize("field", ["bucket", "key", "version"])
def test_reference_cannot_escape_bucket_tenant_project_or_version(cfg, row, field):
    store, client, reference = archived(cfg, row)
    client.calls.clear()
    reference[field] = "null" if field == "version" else "other"
    with pytest.raises(RetentionError):
        store.read(row, reference)
    assert not client.calls


def test_mutated_row_scope_cannot_use_original_reference(cfg, row):
    store, client, reference = archived(cfg, row)
    row["project_id"] = "c79f4148-7b68-467a-9a70-0ff2d20cff7c"
    client.calls.clear()
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        store.read(row, reference)
    assert not client.calls


def test_ambiguous_committed_upload_recovers_exact_version_without_duplicate(cfg, row):
    client = FakeS3()
    client.upload_outcomes = ["commit-timeout"]
    store = PrivateObjectStore(client, cfg)
    first = store.archive(row)
    second = store.archive(row)
    assert first == second
    assert len(client.objects) == 1
    gets = [args for op, args in client.calls if op == "get_object"]
    assert all(args["VersionId"] == first["version"] for args in gets)


def test_ambiguous_missing_upload_retries_once_with_conditional_write(cfg, row):
    client = FakeS3()
    client.upload_outcomes = [TimeoutError("private bytes"), "success"]
    reference = PrivateObjectStore(client, cfg).archive(row)
    assert reference["verified"] is True
    puts = [args for op, args in client.calls if op == "put_object"]
    assert len(puts) == 2 and all(args["IfNoneMatch"] == "*" for args in puts)


def test_repeated_ambiguous_write_is_bounded_and_fails(cfg, row):
    client = FakeS3()
    client.upload_outcomes = [TimeoutError(), TimeoutError()]
    with pytest.raises(RetentionError, match="archive_unavailable"):
        PrivateObjectStore(client, cfg).archive(row)
    assert len([op for op, _ in client.calls if op == "put_object"]) == 2


def test_ambiguous_head_permission_denial_never_retries_upload(cfg, row):
    client = FakeS3()
    client.upload_outcomes = [TimeoutError()]
    client.failures["head_object"] = S3Error("AccessDenied")
    with pytest.raises(RetentionError, match="archive_unavailable"):
        PrivateObjectStore(client, cfg).archive(row)
    assert len([op for op, _ in client.calls if op == "put_object"]) == 1


def test_existing_wrong_object_is_never_accepted_as_retry_success(cfg, row):
    store, client, ref = archived(cfg, row)
    client.objects[(ref["key"], ref["version"])]["payload"] = b"wrong stored bytes"
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        store.archive(row)
    assert len(client.objects) == 1 and client.body.closed


@pytest.mark.parametrize("version", [None, "", "null", 123])
def test_upload_response_requires_a_concrete_version_before_readback(cfg, row, version):
    client = FakeS3()
    client.put_object = lambda **kwargs: {"VersionId": version}
    with pytest.raises(RetentionError, match="archive_versioning_required"):
        PrivateObjectStore(client, cfg).archive(row)
    assert client.body is None


def test_missing_body_and_read_permission_denial_fail_without_receipt(cfg, row):
    store, client, ref = archived(cfg, row)
    original_get = client.get_object
    def no_body(**args):
        response = original_get(**args)
        response.pop("Body").close()
        return response
    client.get_object = no_body
    with pytest.raises(RetentionError, match="archive_integrity_failed"):
        store.read(row, ref)
    client.failures["get_object"] = S3Error("AccessDenied")
    with pytest.raises(RetentionError, match="archive_unavailable"):
        store.read(row, ref)


def test_policy_revalidated_after_archive_before_each_read(cfg, row):
    store, client, ref = archived(cfg, row)
    client.calls.clear()
    client.public_block["PublicAccessBlockConfiguration"]["BlockPublicPolicy"] = False
    with pytest.raises(RetentionError, match="archive_private_bucket_required"):
        store.read(row, ref)
    assert not any(op == "get_object" for op, _ in client.calls)


def test_empty_artifact_roundtrip_is_valid_when_hash_and_size_match(cfg, row):
    row.update(payload=b"", byte_length=0, sha256=hashlib.sha256(b"").hexdigest())
    store, client, ref = archived(cfg, row)
    assert store.read(row, ref) == b"" and client.body.closed


def test_delete_retained_object_is_deferred_without_aws_mutation(cfg, row):
    store, client, _ = archived(cfg, row)
    client.calls.clear()
    with pytest.raises(RetentionError, match="archive_retention_active") as error:
        store.delete(row)
    assert error.value.status == 409 and not client.calls


@pytest.mark.parametrize("restriction", ["extended", "legal-hold"])
def test_delete_checks_actual_version_retention_and_legal_hold(cfg, row, restriction):
    store, client, ref = expired(cfg, row)
    obj = client.objects[(ref["key"], ref["version"])]
    if restriction == "extended":
        obj["ObjectLockRetainUntilDate"] = datetime.now(timezone.utc) + timedelta(days=1)
    else:
        obj["ObjectLockLegalHoldStatus"] = "ON"
    with pytest.raises(RetentionError, match="archive_retention_active"):
        store.delete(row)
    assert not any(op == "delete_object" for op, _ in client.calls)


@pytest.mark.parametrize("outcome", ["delete", "commit-timeout"])
def test_delete_removes_only_pinned_version_and_proves_absence(cfg, row, outcome):
    store, client, ref = expired(cfg, row)
    other = copy.deepcopy(client.objects[(ref["key"], ref["version"])])
    other["VersionId"] = "newer-version"
    client.objects[(ref["key"], "newer-version")] = other
    client.latest[ref["key"]] = "newer-version"
    client.delete_outcome = outcome
    receipt = store.delete(row)
    assert receipt == {"bucket": ref["bucket"], "key": ref["key"], "version": ref["version"], "deleted": True}
    assert (ref["key"], "newer-version") in client.objects
    assert store.delete(row) == receipt
    deletes = [args for op, args in client.calls if op == "delete_object"]
    assert len(deletes) == 1 and deletes[0]["VersionId"] == ref["version"]
    assert "BypassGovernanceRetention" not in deletes[0]


def test_delete_ack_without_absence_is_not_verified(cfg, row):
    store, client, _ = expired(cfg, row)
    client.delete_outcome = "keep"
    with pytest.raises(RetentionError, match="archive_deletion_unverified"):
        store.delete(row)


def test_delete_permission_denial_is_sanitized(cfg, row):
    store, client, _ = expired(cfg, row)
    client.failures["delete_object"] = S3Error("AccessDenied")
    with pytest.raises(RetentionError, match="archive_unavailable"):
        store.delete(row)


def test_delete_head_permission_denial_is_not_treated_as_absence(cfg, row):
    store, client, _ = expired(cfg, row)
    client.failures["head_object"] = S3Error("AccessDenied")
    with pytest.raises(RetentionError, match="archive_unavailable"):
        store.delete(row)
    assert not any(op == "delete_object" for op, _ in client.calls)


def test_delete_missing_version_is_idempotent_without_touching_other_versions(cfg, row):
    store, client, ref = expired(cfg, row)
    client.objects.pop((ref["key"], ref["version"]))
    assert store.delete(row)["deleted"] is True
    assert not any(op == "delete_object" for op, _ in client.calls)


def test_reconcile_crashed_upload_uses_only_exact_tenant_key(cfg, row):
    row["retention_until"] = datetime.now(timezone.utc) - timedelta(seconds=2)
    client = FakeS3()
    store = PrivateObjectStore(client, cfg)
    reference_a = store.archive({**row, "retention_until": datetime.now(timezone.utc) + timedelta(days=1)})
    other = {**row, "tenant_id": "7d76e1a4-e722-4dd4-9089-ae3edb46590e",
             "project_id": "39c50ee8-8454-4735-9d78-cd7a411642a1",
             "artifact_id": "a21f632c-1d81-46cc-82b7-76426a121899",
             "retention_until": datetime.now(timezone.utc) + timedelta(days=30)}
    reference_b = store.archive(other)
    client.objects[(reference_a["key"], reference_a["version"])]["ObjectLockRetainUntilDate"] = row["retention_until"]
    # The simulated crashed worker never persisted any of these coordinates.
    row.update(object_bucket=None, object_key=None, object_version=None)
    client.calls.clear()

    proof = store.reconcile_delete(row)

    assert proof == {"bucket": cfg.bucket, "key": reference_a["key"],
                     "version": reference_a["version"], "deleted": True,
                     "reconciled": True, "absent": False}
    assert (reference_a["key"], reference_a["version"]) not in client.objects
    assert (reference_b["key"], reference_b["version"]) in client.objects
    object_calls = [(op, args) for op, args in client.calls
                    if op in {"head_object", "get_object", "delete_object"}]
    assert object_calls and all(args["Key"] == reference_a["key"] for _, args in object_calls)
    assert all(args.get("VersionId") != reference_b["version"] for _, args in object_calls)


def test_reconcile_missing_key_records_absence_without_listing(cfg, row):
    row["retention_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    client = FakeS3()
    store = PrivateObjectStore(client, cfg)

    proof = store.reconcile_delete(row)

    assert proof["deleted"] and proof["reconciled"] and proof["absent"]
    assert proof["version"] is None
    assert not any(op in {"list_objects", "list_object_versions", "get_object", "delete_object"}
                   for op, _ in client.calls)


def test_factory_is_lazy_validates_before_client_creation_and_bounds_network(cfg, monkeypatch):
    captured = []
    boto = ModuleType("boto3")
    boto.client = lambda *args, **kwargs: captured.append((args, kwargs)) or FakeS3()
    botocore = ModuleType("botocore.config")
    botocore.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", boto)
    monkeypatch.setitem(sys.modules, "botocore.config", botocore)
    assert not captured
    with pytest.raises(RetentionError):
        create_object_store(replace(cfg, enabled=False))
    assert not captured
    assert isinstance(create_object_store(cfg), PrivateObjectStore)
    args, kwargs = captured[0]
    assert args == ("s3",) and kwargs["region_name"] == cfg.region
    assert kwargs["config"] == {"connect_timeout": 5, "read_timeout": 15, "max_pool_connections": 4,
                                "ignore_configured_endpoint_urls": True,
                                "retries": {"mode": "standard", "total_max_attempts": 2}}
    assert "endpoint_url" not in kwargs


def test_real_sdk_stubber_validates_encrypted_retained_request_shapes(cfg, row):
    """Uses boto3's actual model, but Stubber prevents all network requests."""
    boto3 = pytest.importorskip("boto3")
    stub = pytest.importorskip("botocore.stub")
    from botocore.config import Config

    client = boto3.client("s3", region_name=cfg.region, aws_access_key_id="offline-fixture",
                          aws_secret_access_key="offline-fixture",
                          config=Config(ignore_configured_endpoint_urls=True))
    owner = {"Bucket": cfg.bucket, "ExpectedBucketOwner": cfg.expected_owner}
    key = f"{cfg.prefix}/{row['tenant_id']}/{row['project_id']}/{row['artifact_id']}"
    digest = base64.b64encode(bytes.fromhex(row["sha256"])).decode("ascii")
    deadline = (row["retention_until"] + timedelta(seconds=1)).replace(microsecond=0)
    body = TrackedBody(row["payload"])
    with stub.Stubber(client) as calls:
        def policy():
            calls.add_response("get_bucket_versioning", {"Status": "Enabled"}, owner)
            calls.add_response("get_public_access_block", {"PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}, owner)
            calls.add_response("get_bucket_ownership_controls", {"OwnershipControls": {
                "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}, owner)
            calls.add_response("get_object_lock_configuration", {"ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled"}}, owner)

        policy()
        calls.add_response("put_object", {"VersionId": "sdk-version"}, {
            **owner, "Key": key, "Body": row["payload"], "ContentType": "application/octet-stream",
            "ContentLength": row["byte_length"], "ChecksumAlgorithm": "SHA256", "ChecksumSHA256": digest,
            "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": cfg.kms_key,
            "Metadata": {"artifact-id": row["artifact_id"], "sha256": row["sha256"]},
            "IfNoneMatch": "*", "ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": deadline,
        })
        policy()
        calls.add_response("get_object", {
            "Body": body, "VersionId": "sdk-version", "ContentLength": row["byte_length"],
            "ChecksumSHA256": digest, "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": cfg.kms_key,
            "Metadata": {"artifact-id": row["artifact_id"], "sha256": row["sha256"]},
            "ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": deadline,
        }, {**owner, "Key": key, "VersionId": "sdk-version", "ChecksumMode": "ENABLED"})
        assert PrivateObjectStore(client, cfg).archive(row)["verified"] is True
        calls.assert_no_pending_responses()
    assert body.closed
    client.close()
