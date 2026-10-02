"""Offline public-source retention fixtures; no AWS, DB or full app startup."""
import asyncio
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import types
import uuid
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]
PKG = "retention_contract_fixture"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(PKG + "." + name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


for suffix in ("", ".auth", ".routers"):
    package = types.ModuleType(PKG + suffix)
    package.__path__ = []
    sys.modules[package.__name__] = package
configuration = types.ModuleType(PKG + ".config")
configuration.settings = types.SimpleNamespace(internal_tokens=["fixture-internal"],
    api_keys=[], internal_auth_secret=None)
sys.modules[configuration.__name__] = configuration
database = types.ModuleType(PKG + ".db")
def forbidden_db():
    raise AssertionError("Unit fixture must not open a real database")
database.async_session_scope = forbidden_db
sys.modules[database.__name__] = database
load("auth.models", "mindex_api/auth/models.py")
auth = load("auth.internal_auth", "mindex_api/auth/internal_auth.py")
sys.modules[PKG + ".auth"].require_internal_token = auth.require_internal_token
core = load("source_capture", "mindex_api/source_capture.py")
s3 = load("source_capture_s3", "mindex_api/source_capture_s3.py")
routes = load("routers.source_capture", "mindex_api/routers/source_capture.py")


def config(**kwargs):
    return replace(core.CaptureConfig(enabled=True, sources=("fixture-public",),
        bucket="fixture-private-bucket", prefix="qualification/retention",
        kms_key="arn:aws:kms:us-east-1:000000000000:key/fixture-key",
        expected_owner="000000000000", region="us-east-1"), **kwargs)


def capture_row(payload=b' {"fixture": true}\n'):
    return {"capture_id": uuid.uuid4(), "sha256": hashlib.sha256(payload).hexdigest(),
            "payload": payload, "byte_length": len(payload), "state": "archiving"}


class ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects, self.calls = {}, []
        self.ambiguous_once = False
        self.versioning = "Enabled"
        self.corrupt = False
        self.response_overrides = {}

    def get_bucket_versioning(self, **kwargs):
        self.calls.append(("versioning", kwargs))
        return {"Status": self.versioning}

    def put_object(self, **kwargs):
        self.calls.append(("put", kwargs))
        key = kwargs["Key"]
        if key in self.objects:
            raise ClientError("PreconditionFailed")
        self.objects[key] = kwargs
        if self.ambiguous_once:
            self.ambiguous_once = False
            raise TimeoutError("fixture ambiguous write")
        return {"VersionId": "fixture-version"}

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        return {"VersionId": "fixture-version"}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        saved = self.objects[kwargs["Key"]]
        data = saved["Body"]
        result = {"VersionId": "fixture-version", "ContentLength": len(data),
            "ServerSideEncryption": saved["ServerSideEncryption"],
            "SSEKMSKeyId": saved["SSEKMSKeyId"], "Metadata": saved["Metadata"],
            "Body": io.BytesIO(b"corrupt" if self.corrupt else data)}
        result.update(self.response_overrides)
        return result


@pytest.mark.parametrize("change,code", [
    ({"source_id": "not-configured"}, "source_not_allowed"),
    ({"source_id": "../fixture-public"}, "source_not_allowed"),
    ({"idempotency_key": ""}, "invalid_idempotency_key"),
    ({"observed_at": "2026-01-01"}, "invalid_observed_at"),
    ({"observed_at": "0001-01-01T00:00:00+14:00"}, "invalid_observed_at"),
    ({"observed_at": "9999-12-31T23:59:59-14:00"}, "invalid_observed_at"),
    ({"media_type": "text/html"}, "unsupported_media_type"),
    ({"content_encoding": "unknown"}, "unsupported_content_encoding"),
])
def test_metadata_rejects_unapproved_inputs(change, code):
    args = dict(source_id="fixture-public", idempotency_key="observation:1",
                media_type="application/json", content_encoding="identity", observed_at=None)
    with pytest.raises(core.CaptureError, match=code):
        core.capture_metadata(**{**args, **change}, config=config())


def test_equivalent_observation_time_is_canonical_and_unknown_stays_unknown():
    args = ("fixture-public", "one", "application/json", "identity")
    a = core.capture_metadata(*args, "2026-01-01T00:00:00Z", config())
    b = core.capture_metadata(*args, "2026-01-01T01:00:00+01:00", config())
    assert a["metadata_sha256"] == b["metadata_sha256"]
    assert core.capture_metadata(*args, None, config())["observed_at"] is None


@pytest.mark.parametrize("change", [{"enabled": False}, {"sources": ()},
    {"max_pending_bytes": 1}, {"max_pending_count": 0}, {"max_payload_bytes": 32 * 1024 * 1024}])
def test_configuration_fails_closed(change):
    with pytest.raises(core.CaptureError):
        config(**change).admission()


def test_s3_conditional_private_parameters_and_read_back_exact_bytes():
    client, row = FakeS3(), capture_row()
    store = s3.CaptureObjectStore(client, config())
    reference = store.archive(row)
    assert store.read(row, reference) == row["payload"]
    put = next(call for name, call in client.calls if name == "put")
    assert put["IfNoneMatch"] == "*" and put["ServerSideEncryption"] == "aws:kms"
    assert put["ExpectedBucketOwner"] == "000000000000" and "ACL" not in put
    assert put["Body"] == row["payload"] and reference["version"] == "fixture-version"
    assert next(call for name, call in client.calls if name == "get")["VersionId"] == reference["version"]


def test_ambiguous_upload_retries_existing_object_without_overwrite():
    client, row = FakeS3(), capture_row()
    client.ambiguous_once = True
    store = s3.CaptureObjectStore(client, config())
    with pytest.raises(core.CaptureError, match="archive_unavailable"):
        store.archive(row)
    result = store.archive(row)
    assert result["version"] == "fixture-version" and len(client.objects) == 1
    assert any(name == "head" for name, _ in client.calls)


@pytest.mark.parametrize("override", [{"VersionId": "wrong"}, {"ContentLength": 999},
    {"ServerSideEncryption": "AES256"}, {"SSEKMSKeyId": "wrong"}, {"Metadata": {}}])
def test_s3_verification_rejects_wrong_evidence(override):
    client, row = FakeS3(), capture_row()
    client.response_overrides = override
    with pytest.raises(core.CaptureError, match="capture_integrity_failed"):
        s3.CaptureObjectStore(client, config()).archive(row)


def test_corrupt_archived_read_is_not_successful_empty_response():
    client, row = FakeS3(), capture_row()
    store = s3.CaptureObjectStore(client, config())
    reference = store.archive(row)
    client.corrupt = True
    with pytest.raises(core.CaptureError, match="capture_integrity_failed"):
        store.read(row, reference)


def test_local_payload_corruption_and_unversioned_bucket_are_rejected():
    client, row = FakeS3(), capture_row()
    store = s3.CaptureObjectStore(client, config())
    with pytest.raises(core.CaptureError, match="capture_integrity_failed"):
        store.archive({**row, "payload": b"different"})
    assert client.calls == []
    client.versioning = "Suspended"
    with pytest.raises(core.CaptureError, match="archive_versioning_required"):
        store.archive(row)
    assert not any(name == "put" for name, _ in client.calls)


def test_reference_cannot_redirect_to_another_bucket():
    client, row = FakeS3(), capture_row()
    store = s3.CaptureObjectStore(client, config())
    ref = store.archive(row)
    with pytest.raises(core.CaptureError, match="capture_integrity_failed"):
        store.read(row, {**ref, "bucket": "other"})


class FixtureRepository:
    def __init__(self, cfg=None):
        self.config = cfg or config()
        self.calls = []

    async def accept(self, service, metadata, payload):
        self.calls.append((service, metadata, payload))
        return {"state": "pending_archive", "cloud_verified": False,
                "capture_id": str(uuid.uuid4())}, True

    async def get(self, capture_id):
        return {**capture_row(), "capture_id": capture_id, "state": "pending_archive"}


def request(repository, method="POST", headers=None, body=b'{"fixture":true}', path="/source-captures/fixture-public"):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.capture_repository] = lambda: repository
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            return await client.request(method, path, headers=headers or {}, content=body)
    return asyncio.run(invoke())


@pytest.mark.parametrize("header", [{}, {"X-Internal-Token": "rejected"}])
def test_actual_internal_auth_rejects_before_capture(header):
    repository = FixtureRepository()
    assert request(repository, headers=header).status_code == 401
    assert repository.calls == []


def test_authenticated_capture_preserves_bytes_and_pending_status():
    repository = FixtureRepository()
    raw = b' { "fixture": true }\n'
    response = request(repository, body=raw, headers={"X-Internal-Token": "fixture-internal",
        "Idempotency-Key": "fixture:one", "Content-Type": "application/json"})
    assert response.status_code == 202 and response.json()["cloud_verified"] is False
    assert repository.calls[0][0] == "internal" and repository.calls[0][2] == raw
    assert response.headers["cache-control"] == "private, no-store"


def test_oversize_is_rejected_before_repository_write():
    repository = FixtureRepository(config(max_payload_bytes=4))
    response = request(repository, headers={"X-Internal-Token": "fixture-internal", "Idempotency-Key": "one"})
    assert response.status_code == 413 and repository.calls == []


def test_storage_errors_are_sanitized():
    repository = FixtureRepository()
    async def fail(*_args):
        raise RuntimeError("private account secret SQL")
    repository.accept = fail
    response = request(repository, headers={"X-Internal-Token": "fixture-internal", "Idempotency-Key": "one"})
    assert response.status_code == 503 and response.json() == {"detail": "capture_storage_unavailable"}


def test_pending_raw_read_checks_bytes_and_is_download_only():
    repository = FixtureRepository()
    response = request(repository, method="GET", path=f"/source-captures/{uuid.uuid4()}/raw",
        headers={"X-Internal-Token": "fixture-internal"})
    assert response.status_code == 200 and response.content == capture_row()["payload"]
    assert response.headers["x-capture-state"] == "pending_archive"
    assert "content-encoding" not in response.headers
    assert response.headers["content-disposition"].startswith("attachment;")
