"""FormSpace durable scalar request contract. No implicit coercion or extra fields."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import rfc8785

CONTRACT_VERSION = "formspace.durable/v1"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_PENDING = 32
MAX_RUNNING = 16
MAX_PROJECT_RUNNING = 4
LEASE_SECONDS = 120
MAX_ATTEMPTS = 8
Text = Annotated[str, Field(min_length=1, max_length=128)]
Unit = Annotated[str, Field(min_length=1, max_length=64)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")]
Number = Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]


class FormSpaceError(Exception):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def clean_labels(self):
        for value in self.__dict__.values():
            if isinstance(value, str) and (value.strip() != value or
                    any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
                raise ValueError("trimmed labels without control characters required")
        return self


class Axis(Strict):
    name: Text
    unit: Unit


class Axes(Strict):
    input: Axis
    state: Axis
    time: Axis


class Metric(Strict):
    kind: Literal["absolute-scalar-residual"]
    unit: Unit
    scope: Literal["within-chart-revision"]


class Chart(Strict):
    schema: Literal["formspace.chart-revision/v1"]
    chart_id: Identifier
    revision: Annotated[int, Field(ge=1, le=9007199254740991)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    axes: Axes
    mask_policy: Literal["abstain"]
    metric: Metric


class Source(Strict):
    kind: Literal["USER_IMPORT", "TEACHING_FIXTURE"]
    classification: Literal["PRIVATE"]
    reference: Annotated[str, Field(min_length=1, max_length=2048)]
    license: Annotated[str, Field(min_length=1, max_length=256)]


def timestamp(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
        raise ValueError("UTC timestamp required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Sample(Strict):
    sample_id: Identifier
    observed_at: Annotated[str, Field(max_length=32)]
    available_at: Annotated[str, Field(max_length=32)]
    value: Number | None
    masked: bool


class Dataset(Strict):
    schema: Literal["formspace.imported-series/v1"]
    dataset_id: Identifier
    value_unit: Unit
    source: Source
    samples: Annotated[list[Sample], Field(min_length=1, max_length=4096)]


class Parameters(Strict):
    dt: Annotated[float, Field(ge=1e-6, le=1000, allow_inf_nan=False)]
    a: Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]
    b: Annotated[float, Field(ge=-1e6, le=1e6, allow_inf_nan=False)]
    h0: Number
    perturbation_index: Annotated[int, Field(ge=0, le=4095)]
    perturbation_delta: Number
    residual_threshold: Annotated[float, Field(ge=0, le=1e6, allow_inf_nan=False)]


class Experiment(Strict):
    schema: Literal["formspace.experiment.request/v1"]
    engine_mode: Literal["scalar-native-v1"]
    as_of: Annotated[str, Field(max_length=32)]
    chart_revision: Chart
    dataset: Dataset
    parameters: Parameters

    @model_validator(mode="after")
    def consistent(self):
        cutoff = timestamp(self.as_of)
        if cutoff > datetime.now(timezone.utc):
            raise ValueError("as_of exceeds server time")
        chart = self.chart_revision
        if chart.axes.time.unit != "s" or chart.axes.input.unit != self.dataset.value_unit:
            raise ValueError("inconsistent units")
        if chart.metric.unit != chart.axes.state.unit:
            raise ValueError("residual unit must match state")
        if self.parameters.perturbation_index >= len(self.dataset.samples):
            raise ValueError("perturbation index outside samples")
        selected = self.dataset.samples[self.parameters.perturbation_index].value
        if selected is not None and abs(selected + self.parameters.perturbation_delta) > 1e6:
            raise ValueError("perturbed input exceeds bound")
        identifiers = set()
        previous = None
        for sample in self.dataset.samples:
            observed, available = timestamp(sample.observed_at), timestamp(sample.available_at)
            if sample.sample_id in identifiers or (previous and observed <= previous):
                raise ValueError("sample IDs and observation times must be unique and ordered")
            identifiers.add(sample.sample_id)
            previous = observed
            if observed > available or available > cutoff:
                raise ValueError("sample outside as-of availability")
            if (sample.value is None) != sample.masked:
                raise ValueError("masked samples must have null value")
        return self


class Admission(Strict):
    request: Experiment


class Lease(Strict):
    lease_token: Annotated[str, Field(min_length=32, max_length=128)]
    fence: Annotated[int, Field(ge=1)]


class Computed(Lease):
    result_json: Annotated[str, Field(min_length=2, max_length=MAX_RESULT_BYTES)]
    output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Failed(Lease):
    error_code: Literal["engine_failed", "invalid_result", "worker_timeout", "retention_unavailable"]


class Claim(Strict):
    worker_id: Identifier


def canonical(value: dict) -> bytes:
    return rfc8785.dumps(value)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def idempotency(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise FormSpaceError("invalid_idempotency_key", 422)
    return value


def receipt(row: dict) -> dict:
    return {
        "schema": "formspace.job/v1",
        "contract_version": CONTRACT_VERSION, "job_id": str(row["job_id"]),
        "tenant_id": str(row["tenant_id"]),
        "project_id": str(row["project_id"]), "state": row["state"],
        "engine_mode": "scalar-native-v1", "created_at": row["created_at"],
        "request_hash": row["request_hash"], "chart_hash": row["chart_hash"],
        "dataset_hash": row["dataset_hash"], "error_code": row.get("error_code"),
        "artifact": {"state": row.get("artifact_state") or "pending",
                     "artifact_id": str(row["artifact_id"]) if row.get("artifact_id") else None,
                     "sha256": row.get("output_sha256")},
        "memory": {"state": "pending", "reference_state": row.get("memory_state") or "pending",
                   "memory_id": str(row["memory_id"]) if row.get("memory_id") else None,
                   "index_state": "pending", "learned": False},
        "replica": {"state": row.get("replica_state") or "unavailable"},
    }
