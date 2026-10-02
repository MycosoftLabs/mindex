"""Explicit in-memory boundary fixtures; these do NOT qualify PostgreSQL/AWS."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mindex_api.formspace.contracts import Admission, FormSpaceError, canonical, digest, receipt
from mindex_api.formspace.service import FormSpaceService, strict_json
from mindex_api.routers import formspace_durable as routes

CODE = "a" * 64
TENANT = "11111111-1111-4111-8111-111111111111"
PROJECT = "22222222-2222-4222-8222-222222222222"


def experiment():
    return {"schema": "formspace.experiment.request/v1", "engine_mode": "scalar-native-v1",
        "as_of": "2026-01-01T00:00:01.000Z",
        "chart_revision": {"schema": "formspace.chart-revision/v1", "chart_id": "fixture/chart",
            "revision": 1, "title": "Explicit fixture", "axes": {
                "input": {"name": "Input", "unit": "µg"},
                "state": {"name": "State", "unit": "mg"},
                "time": {"name": "Time", "unit": "s"}}, "mask_policy": "abstain",
            "metric": {"kind": "absolute-scalar-residual", "unit": "mg", "scope": "within-chart-revision"}},
        "dataset": {"schema": "formspace.imported-series/v1", "dataset_id": "fixture/data",
            "value_unit": "µg", "source": {"kind": "TEACHING_FIXTURE", "classification": "PRIVATE",
            "reference": "local test fixture", "license": "test only"}, "samples": [
                {"sample_id": "s1", "observed_at": "2026-01-01T00:00:00.000Z",
                 "available_at": "2026-01-01T00:00:00.000Z", "value": 1, "masked": False}]},
        "parameters": {"dt": 0.1, "a": 0, "b": 1, "h0": 0, "perturbation_index": 0,
                       "perturbation_delta": 1, "residual_threshold": 0.05}}


@dataclass(frozen=True)
class FixturePrincipal:
    issuer: str = "https://fixture.invalid"
    subject: str = "alice"
    tenant_id: str = TENANT
    project_id: str = PROJECT


class FixtureJournal:
    """Deliberate fake durable journal shared by restarted app fixture instances."""
    def __init__(self):
        self.rows = {}
        self.members = {FixturePrincipal(), FixturePrincipal(subject="bob"),
                        FixturePrincipal(project_id="33333333-3333-4333-8333-333333333333")}

    def authorize(self, p):
        if p not in self.members:
            raise FormSpaceError("membership_required", 403)

    def owner(self, row):
        return FixturePrincipal(**{key: row[key] for key in ("issuer", "subject", "tenant_id", "project_id")})

    async def admit(self, p, key, request):
        from mindex_api.formspace.contracts import idempotency
        idempotency(key)
        self.authorize(p)
        request_hash = digest(canonical(request))
        for row in self.rows.values():
            if self.owner(row) == p and row["key"] == key:
                if request_hash != row["request_hash"]:
                    raise FormSpaceError("idempotency_conflict", 409)
                return receipt(row), False
        for row in self.rows.values():
            prior = row["request"]["chart_revision"]
            chart = request["chart_revision"]
            if self.owner(row) == p and prior["chart_id"] == chart["chart_id"] and prior["revision"] == chart["revision"] and prior != chart:
                raise FormSpaceError("chart_revision_conflict", 409)
        row = {**p.__dict__, "job_id": str(uuid4()), "key": key, "state": "admitted",
               "created_at": datetime.now(timezone.utc), "request_hash": request_hash,
               "chart_hash": digest(canonical(request["chart_revision"])),
               "dataset_hash": digest(canonical(request["dataset"])), "request": deepcopy(request),
               "output_bytes": None, "output_sha256": None, "artifact_id": None,
               "artifact_state": "pending", "memory_state": "pending"}
        self.rows[row["job_id"]] = row
        return receipt(row), True

    async def get(self, p, job_id):
        self.authorize(p)
        row = self.rows.get(job_id)
        if row is None or self.owner(row) != p:
            raise FormSpaceError("job_not_found", 404)
        return row.copy()

    async def list(self, p, limit):
        self.authorize(p)
        return [receipt(row) for row in self.rows.values() if self.owner(row) == p][:limit]

    async def cancel(self, p, job_id):
        await self.get(p, job_id)
        self.rows[job_id]["state"] = "cancelled"
        return receipt(self.rows[job_id])

    async def leased(self, job_id, lease):
        row = self.rows[job_id]
        self.authorize(self.owner(row))
        if lease != {"lease_token": "l" * 32, "fence": 1} or row["state"] not in ("running", "archiving"):
            raise FormSpaceError("lease_lost", 409)
        return row.copy()

    async def computed(self, job_id, lease, payload, sha):
        await self.leased(job_id, lease)
        self.rows[job_id].update(state="archiving", output_bytes=payload, output_sha256=sha)
        return receipt(self.rows[job_id])

    async def memory(self, p, job_id, state, memory_id):
        await self.get(p, job_id)
        self.rows[job_id].update(memory_state=state, memory_id=memory_id)

    async def claim(self, worker_id):
        for row in self.rows.values():
            if row["state"] not in ("admitted", "archiving"):
                continue
            self.authorize(self.owner(row))
            if row["state"] == "admitted":
                row["state"] = "running"
            return {"job": receipt(row), "request": row["request"],
                    "has_output": row["output_bytes"] is not None,
                    "lease": {"token": "l" * 32, "fence": 1,
                              "expires_at": "2099-01-01T00:00:00.000Z"}}

    async def heartbeat(self, job_id, lease):
        return receipt(await self.leased(job_id, lease))

    async def retained(self, job_id, lease, artifact, verified=False):
        await self.leased(job_id, lease)
        self.rows[job_id].update(artifact_id=artifact["artifact_id"], artifact_state=artifact["state"],
                                state="completed" if verified else "archiving")
        if verified:
            self.rows[job_id]["output_bytes"] = None
        return receipt(self.rows[job_id])


def fixture_app(journal=None):
    journal = journal or FixtureJournal()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/mindex")
    app.state.formspace_service = FormSpaceService(journal, None, CODE)

    async def fixture_identity(request: Request):
        token = request.headers.get("Authorization", "")
        if token not in ("Bearer fixture-alice", "Bearer fixture-bob"):
            raise HTTPException(401, detail="explicit fixture identity denied")
        p = FixturePrincipal(subject=token.removeprefix("Bearer fixture-"),
                             tenant_id=request.headers.get("X-Tenant-Id", ""),
                             project_id=request.headers.get("X-Project-Id", ""))
        journal.authorize(p)
        return p
    app.dependency_overrides[routes.principal] = fixture_identity
    return app, journal


def headers(user="alice", project=PROJECT):
    return {"Authorization": "Bearer fixture-" + user, "X-Tenant-Id": TENANT,
            "X-Project-Id": project, "Idempotency-Key": "request-1"}


def test_ecmascript_canonical_number_and_unicode_golden():
    assert canonical({"µ": 1.0, "z": -0.0, "tiny": 1e-7, "large": 1e20}) == (
        '{"large":100000000000000000000,"tiny":1e-7,"z":0,"µ":1}').encode()


@pytest.mark.parametrize("change", [
    lambda v: v.update(owner="forged"),
    lambda v: v.update(engine_mode="trained-model"),
    lambda v: v["parameters"].update(dt=True),
    lambda v: v["parameters"].update(dt="0.1"),
    lambda v: v["parameters"].update(a=float("nan")),
    lambda v: v["dataset"]["samples"][0].update(value=None, masked=False),
    lambda v: v["dataset"]["samples"][0].update(available_at="2027-01-01T00:00:00.000Z"),
    lambda v: v["dataset"].update(value_unit="mismatched"),
    lambda v: v["chart_revision"]["metric"].update(unit="mismatched"),
    lambda v: v.update(as_of="2099-01-01T00:00:00.000Z"),
    lambda v: v["parameters"].update(perturbation_index=1),
    lambda v: v["chart_revision"].update(title=" trailing "),
])
def test_direct_contract_rejects_malformed_data(change):
    value = experiment()
    change(value)
    with pytest.raises(ValidationError):
        Admission.model_validate({"request": value})


def test_strict_json_duplicates_nonfinite():
    for value in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
        with pytest.raises(ValueError):
            strict_json(value)


def test_two_user_two_project_no_enumeration_mutation_and_restart_replay():
    app, journal = fixture_app()
    client = TestClient(app)
    url = "/api/mindex/formspace/v1/jobs"
    admitted = client.post(url, headers=headers(), json={"request": experiment()})
    assert admitted.status_code == 202, admitted.text
    job_id = admitted.json()["job"]["job_id"]
    assert admitted.headers["cache-control"] == "private, no-store"
    for denied in (headers("bob"), headers(project="33333333-3333-4333-8333-333333333333")):
        assert client.get(url, headers=denied).json()["jobs"] == []
        for method, suffix in (("get", ""), ("get", "/result"), ("get", "/input"), ("post", "/cancel")):
            assert getattr(client, method)(url + "/" + job_id + suffix, headers=denied).status_code == 404
    restarted = TestClient(fixture_app(journal)[0])
    replay = restarted.post(url, headers=headers(), json={"request": experiment()})
    assert replay.status_code == 200
    assert replay.json()["job"]["job_id"] == job_id
    restored = restarted.get(url + "/" + job_id + "/input", headers=headers())
    assert restored.content == canonical(experiment())
    assert restored.headers["X-Input-Durability"] == "postgres_committed"
    assert restored.headers["X-Input-SHA256"] == digest(restored.content)
    changed = experiment()
    changed["parameters"]["dt"] = 0.2
    assert restarted.post(url, headers=headers(), json={"request": changed}).status_code == 409
    changed = experiment()
    changed["chart_revision"]["title"] = "Different same revision"
    assert restarted.post(url, headers={**headers(), "Idempotency-Key": "new"},
                          json={"request": changed}).status_code == 409


@pytest.mark.parametrize("token", ["", "Bearer expired", "Bearer invalid", "service-key"])
def test_direct_identity_cannot_use_user_header_or_service_key(token):
    app, _ = fixture_app()
    response = TestClient(app).post("/api/mindex/formspace/v1/jobs",
        headers={**headers(), "Authorization": token, "X-User-Id": "alice", "X-API-Key": "service"},
        json={"request": experiment()})
    assert response.status_code == 401


def test_worker_requires_independent_credential(monkeypatch):
    app, _ = fixture_app()
    client = TestClient(app)
    monkeypatch.setenv("FORMSPACE_WORKER_TOKEN", "k" * 32)
    assert client.post("/api/mindex/formspace/v1/worker/claim", headers=headers(),
                       json={"worker_id": "worker"}).status_code == 401


def test_compressed_and_oversize_input_rejected():
    app, _ = fixture_app()
    client = TestClient(app)
    url = "/api/mindex/formspace/v1/jobs"
    assert client.post(url, headers={**headers(), "Content-Encoding": "gzip"},
                       content=b"compressed").status_code == 415
    assert client.post(url, headers=headers(), content=b"a" * (1024 * 1024 + 1)).status_code == 413


def result_for(request):
    result = {"schema": "formspace.experiment.result/v1", "engine_mode": "scalar-native-v1",
        "classification": "PRIVATE", "status": "computed", "reason": None,
        "chart_id": request["chart_revision"]["chart_id"],
        "chart_revision": 1, "dataset_id": request["dataset"]["dataset_id"],
        "data_origin": "TEACHING_FIXTURE", "as_of": request["as_of"], "parameters": request["parameters"],
        "sample_ids": ["s1"], "masked_sample_ids": [], "baseline_trajectory": [0.1],
        "perturbed_trajectory": [0.2], "residuals_after_perturbation": [0.1],
        "first_crossing_index": None, "first_crossing_elapsed_seconds": None,
        "baseline_final_state": 0.1, "perturbed_final_state": 0.2,
        "interpretation": "Explicit fixture only",
        "hashes": {"code": CODE, "input": digest(canonical(request)),
                   "chart_revision": digest(canonical(request["chart_revision"])),
                   "dataset": digest(canonical(request["dataset"])),
                   "parameters": digest(canonical(request["parameters"]))}}
    result["hashes"]["output"] = digest(canonical(result))
    return result


@pytest.mark.asyncio
async def test_compute_exact_bytes_lineage_cancellation_and_membership_fence():
    journal = FixtureJournal()
    request = experiment()
    admitted, _ = await journal.admit(FixturePrincipal(), "one", request)
    job_id = admitted["job_id"]
    journal.rows[job_id]["state"] = "running"
    service = FormSpaceService(journal, None, CODE)
    raw = canonical(result_for(request))
    body = {"lease_token": "l" * 32, "fence": 1, "result_json": raw.decode(),
            "output_sha256": digest(raw)}
    assert (await service.computed(job_id, body))["state"] == "archiving"
    assert journal.rows[job_id]["output_bytes"] == raw
    with pytest.raises(FormSpaceError, match="output_hash_mismatch"):
        await service.computed(job_id, {**body, "output_sha256": "b" * 64})
    invalid = result_for(request)
    invalid["hashes"]["code"] = "c" * 64
    altered = canonical(invalid)
    with pytest.raises(FormSpaceError, match="invalid_result"):
        await service.computed(job_id, {**body, "result_json": altered.decode(), "output_sha256": digest(altered)})
    await journal.cancel(FixturePrincipal(), job_id)
    with pytest.raises(FormSpaceError, match="lease_lost"):
        await service.computed(job_id, body)
    journal.rows[job_id]["state"] = "running"
    journal.members.remove(FixturePrincipal())
    with pytest.raises(FormSpaceError, match="membership_required"):
        await service.computed(job_id, body)


@pytest.mark.asyncio
async def test_result_download_requires_exact_verified_bytes_and_version():
    journal = FixtureJournal()
    admitted, _ = await journal.admit(FixturePrincipal(), "one", experiment())
    job_id = admitted["job_id"]
    artifact_id = str(uuid4())
    raw = b'{"explicit":"fixture"}'
    journal.rows[job_id].update(state="completed", artifact_state="verified",
                               artifact_id=artifact_id, output_sha256=digest(raw))
    class FixtureRetention:
        repository = None
        async def content(self, p, identifier):
            assert identifier == artifact_id
            return {"sha256": digest(raw)}, raw
        async def get(self, p, identifier):
            return {"state": "verified", "object_version": "fixture-version"}
    retained = FixtureRetention()
    retained.repository = retained
    service = FormSpaceService(journal, retained, CODE)
    assert await service.result(FixturePrincipal(), job_id) == (raw, digest(raw), "fixture-version")
    journal.rows[job_id]["output_sha256"] = "f" * 64
    with pytest.raises(FormSpaceError, match="result_integrity_failed"):
        await service.result(FixturePrincipal(), job_id)


def test_shared_dependency_absence_fails_closed(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.delenv("FORMSPACE_DURABLE_ENABLED", raising=False)
    response = TestClient(app).get("/formspace/v1/jobs", headers=headers())
    assert response.status_code == 503


class FixtureRetention:
    """Explicit fake archive/memory provider: no AWS, no MYCA, no cloud durability."""
    config = object()
    def __init__(self):
        self.repository = self
        self.state = "pending"
        self.payload = None
        self.artifact_id = str(uuid4())
        self.memory_fails = True
        self.after_read = None

    async def admit(self, p, metadata, payload):
        assert metadata["kind"] == "artifact" and metadata["key"].startswith("formspace:")
        if self.payload is not None:
            assert self.payload == payload
        self.payload = payload
        return {"artifact_id": self.artifact_id, "sha256": digest(payload), "state": self.state}, True

    async def content(self, p, artifact_id):
        assert artifact_id == self.artifact_id and self.state == "verified"
        if self.after_read:
            self.after_read()
        return {"sha256": digest(self.payload)}, self.payload

    async def get(self, p, artifact_id):
        return {"artifact_id": self.artifact_id, "state": self.state, "object_version": "fixture-only-v1"}

    async def remember(self, p, artifact_id, summary):
        if self.memory_fails:
            raise FormSpaceError("memory_unavailable")
        return {"memory_id": str(uuid4()), "reference_verified": True,
                "artifact_sha256": digest(self.payload), "index_state": "pending"}


def fixture_metadata(kind, key, media_type, source_event, config):
    return {"kind": kind, "key": key}


def golden_chain():
    folder = Path(__file__).parent / "fixtures/formspace"
    return (strict_json((folder / "chain-request.json").read_bytes()),
            (folder / "chain-result.json").read_bytes(),
            strict_json((folder / "chain-fixture-metadata.json").read_bytes()))


def test_real_typescript_golden_to_router_compute_archive_readback_memory_retry(monkeypatch):
    request, output, metadata = golden_chain()
    app, journal = fixture_app()
    retained = FixtureRetention()
    app.state.formspace_service = FormSpaceService(journal, retained, metadata["engine_code_sha256"],
                                                  admission_metadata_factory=fixture_metadata)
    client = TestClient(app)
    monkeypatch.setenv("FORMSPACE_WORKER_TOKEN", "k" * 32)
    worker_headers = {"X-FormSpace-Worker-Token": "k" * 32}
    base = "/api/mindex/formspace/v1"
    admitted = client.post(base + "/jobs", headers=headers(), json={"request": request})
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()["job"]["request_hash"] == metadata["request_sha256"]
    job_id = admitted.json()["job"]["job_id"]
    claim = client.post(base + "/worker/claim", headers=worker_headers, json={"worker_id": "golden"}).json()
    assert claim["request"] == request and claim["has_output"] is False
    lease = {"lease_token": claim["lease"]["token"], "fence": claim["lease"]["fence"]}
    worker_url = base + "/worker/jobs/" + job_id
    assert client.post(worker_url + "/heartbeat", headers=worker_headers, json=lease).status_code == 200
    computed = client.post(worker_url + "/computed", headers=worker_headers,
        json={**lease, "result_json": output.decode(), "output_sha256": digest(output)})
    assert computed.status_code == 200, computed.text
    assert computed.json()["state"] == "archiving"
    assert journal.rows[job_id]["output_bytes"] == output
    pending = client.post(worker_url + "/reconcile", headers=worker_headers, json=lease)
    assert pending.status_code == 200, pending.text
    assert pending.json()["state"] == "archiving"
    assert client.get(base + "/jobs/" + job_id + "/result", headers=headers()).status_code == 409
    # Explicit simulated provider verification; never represent this as AWS proof.
    retained.state = "verified"
    complete = client.post(worker_url + "/reconcile", headers=worker_headers, json=lease)
    assert complete.status_code == 200, complete.text
    assert complete.json()["state"] == "completed"
    assert complete.json()["memory"]["state"] == "pending"
    download = client.get(base + "/jobs/" + job_id + "/result", headers=headers())
    assert download.content == output
    assert download.headers["X-Content-SHA256"] == digest(output)
    assert download.headers["X-Artifact-Version"] == "fixture-only-v1"
    assert strict_json(download.content)["first_crossing_index"] == 21
    retained.memory_fails = False
    memory = client.post(base + "/jobs/" + job_id + "/memory", headers=headers()).json()["job"]["memory"]
    assert memory["reference_state"] == "verified" and memory["memory_id"]
    assert memory["state"] == "pending" and memory["index_state"] == "pending"


@pytest.mark.asyncio
async def test_cancellation_during_archive_readback_cannot_complete():
    request, output, metadata = golden_chain()
    journal = FixtureJournal()
    admitted, _ = await journal.admit(FixturePrincipal(), "golden", request)
    job_id = admitted["job_id"]
    journal.rows[job_id].update(state="archiving", output_bytes=output, output_sha256=digest(output))
    retained = FixtureRetention()
    retained.state = "verified"
    retained.after_read = lambda: journal.rows[job_id].update(state="cancelled")
    service = FormSpaceService(journal, retained, metadata["engine_code_sha256"],
                              admission_metadata_factory=fixture_metadata)
    with pytest.raises(FormSpaceError, match="lease_lost"):
        await service.reconcile(job_id, {"lease_token": "l" * 32, "fence": 1})
    assert journal.rows[job_id]["state"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda v: v.update(first_crossing_index=999999),
    lambda v: v.update(first_crossing_elapsed_seconds=-1),
    lambda v: v.update(baseline_final_state=9999),
    lambda v: v.update(reason="MASKED_INPUT"),
    lambda v: v.update(owner_id="forged"),
    lambda v: v["residuals_after_perturbation"].__setitem__(0, 0),
])
async def test_rehashed_malformed_result_semantics_rejected(change):
    request, output, metadata = golden_chain()
    journal = FixtureJournal()
    admitted, _ = await journal.admit(FixturePrincipal(), "golden", request)
    job_id = admitted["job_id"]
    journal.rows[job_id]["state"] = "running"
    result = strict_json(output)
    change(result)
    del result["hashes"]["output"]
    result["hashes"]["output"] = digest(canonical(result))
    payload = canonical(result)
    service = FormSpaceService(journal, None, metadata["engine_code_sha256"])
    with pytest.raises(FormSpaceError, match="invalid_result"):
        await service.computed(job_id, {"lease_token": "l" * 32, "fence": 1,
            "result_json": payload.decode(), "output_sha256": digest(payload)})
