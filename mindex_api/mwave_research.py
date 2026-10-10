"""Measured-input contract for M-Wave research; never an earthquake predictor."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

PAPER_URL = "https://storage.prod.researchhub.com/uploads/papers/users/100041/845e8316-910c-421d-906c-ff2131e92882/The%20M%20Wave-%20Harnessing%20Mycelium%20Networks%20for%20Earthquake%20Prediction.pdf"
CONTRACT_VERSION = "mwave.research.v1"
MAX_CURRENT_AGE_SECONDS = 600


class BioelectricWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    device_id: str = Field(min_length=1, max_length=128)
    channel_id: str = Field(min_length=1, max_length=64)
    source_kind: Literal["hardware-measurement", "laboratory-measurement", "archived-measurement", "simulation"]
    source_record_id: str = Field(min_length=1, max_length=256)
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    calibration_record_id: str = Field(min_length=1, max_length=256)
    measured_at: AwareDatetime
    sample_rate_hz: float = Field(gt=0, le=10000)
    units: Literal["uV", "mV"]
    values: list[float] = Field(min_length=2, max_length=4096)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    clock_uncertainty_ms: float = Field(ge=0, le=60000)
    snr_db: float
    quality_score: float = Field(ge=0, le=1)
    environment_record_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("values")
    @classmethod
    def finite_values(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Bioelectric samples must be finite measured values.")
        return values


class ResearchInputBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["research-shadow", "research-replay"] = "research-shadow"
    windows: list[BioelectricWindow] = Field(max_length=64)
    seismic_catalog_record_ids: list[str] = Field(default_factory=list, max_length=1024)
    analysis_cutoff: AwareDatetime

    @field_validator("windows")
    @classmethod
    def bound_total_samples(cls, windows: list[BioelectricWindow]) -> list[BioelectricWindow]:
        if sum(len(window.values) for window in windows) > 16384:
            raise ValueError("Research input exceeds the bounded sample budget.")
        return windows


def base_readiness() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "state": "unbound",
        "mode": "research-shadow",
        "prediction_available": False,
        "public_alerts_enabled": False,
        "current_sensor_count": None,
        "hardware_validated": False,
        "measured_seismic": {"source": "USGS", "independent": True, "is_bioelectric_input": False},
        "model": {"state": "unbound", "trained_artifact": None, "evaluation": None},
        "replay": {"state": "unavailable", "source": None, "sample_count": None, "from": None, "to": None, "evaluation": None},
        "input_contract": {"schema_path": "/api/mindex/mwave/input-schema", "evaluate_path": "/api/mindex/mwave/readiness/evaluate", "persistence_bound": False, "max_windows": 64, "max_samples": 16384},
        "paper": {"url": PAPER_URL, "doi": "10.55277/ResearchHub.h62c7qdp.1", "status": "research-hypothesis", "demonstration_prediction_is_model": False},
        "reasons": ["No calibrated, authenticated bioelectric adapter is qualified for this research model.", "No trained predictive artifact or held-out evaluation is bound. Conventional seismic detections do not satisfy these requirements."],
        "pipeline": [
            {"id": "source-validation", "state": "input-schema-prepared"},
            {"id": "time-alignment-and-confounders", "state": "dataset-unbound"},
            {"id": "feature-extraction", "state": "research-only"},
            {"id": "shadow-model", "state": "artifact-unbound"},
            {"id": "held-out-replay-evaluation", "state": "dataset-unbound"},
        ],
    }


def evaluate_research_inputs(batch: ResearchInputBatch, now: datetime | None = None) -> dict[str, Any]:
    """Validate submitted research evidence; never authenticate hardware or infer quakes."""
    now = now or datetime.now(timezone.utc)
    result = base_readiness()
    current: set[str] = set()
    rejected: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    cutoff = batch.analysis_cutoff.astimezone(timezone.utc)
    for index, window in enumerate(batch.windows):
        measured = window.measured_at.astimezone(timezone.utc)
        end = measured.timestamp() + (len(window.values) - 1) / window.sample_rate_hz
        reasons: list[str] = []
        if window.source_kind == "simulation":
            reasons.append("Simulation is separate from measured bioelectric evidence.")
        if end > cutoff.timestamp():
            reasons.append("Samples extend past the declared analysis cutoff; future leakage is rejected.")
        if batch.mode == "research-shadow":
            if window.source_kind != "hardware-measurement":
                reasons.append("Current shadow input requires declared hardware measurement, not laboratory or archive replay.")
            age = now.timestamp() - end
            if age > MAX_CURRENT_AGE_SECONDS or age < -60:
                reasons.append("The declared sensor window is stale or in the future.")
        if window.quality_score < 0.5:
            reasons.append("The declared signal quality is below the research input gate.")
        if reasons:
            rejected.append({"window_index": index, "reasons": reasons})
        else:
            current.add(window.device_id)
            accepted.append({"window_index": index, "source_record_id": window.source_record_id, "sample_count": len(window.values), "units": window.units, "source_kind": window.source_kind})
    result.update({"state": "input-schema-valid" if accepted else "unavailable", "mode": batch.mode,
        "submitted_window_count": len(batch.windows), "accepted_windows": accepted, "rejected_windows": rejected,
        "declared_device_count": len(current), "analysis_cutoff": cutoff.isoformat(), "persisted": False,
        "input_validation_scope": "schema and submitted data quality only; device identity, calibration and source hashes require server-side verification"})
    return result


async def stored_fci_evidence() -> dict[str, Any]:
    """Bounded read-only evidence from existing FCI tables; never substitutes for calibration."""
    async def query() -> list[Any]:
        from .db import get_db_pool

        pool = await get_db_pool()
        async with pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                await connection.execute("SET LOCAL statement_timeout = '2000ms'")
                return await connection.fetch("""
                    SELECT r.id::text AS reading_id, r.device_id::text AS device_id,
                           r.timestamp, r.amplitude_uv, r.snr_db, r.quality_score
                    FROM fci_readings r JOIN fci_devices d ON d.id = r.device_id
                    WHERE r.timestamp >= NOW() - INTERVAL '10 minutes'
                      AND r.timestamp <= NOW() + INTERVAL '60 seconds'
                    ORDER BY r.timestamp DESC LIMIT 128
                """)
    try:
        rows = await asyncio.wait_for(query(), timeout=3)
        measured = [row for row in rows if row["amplitude_uv"] is not None and math.isfinite(float(row["amplitude_uv"]))]
        return {"state": "available" if measured else "empty", "candidate_device_count": len({row["device_id"] for row in measured}), "sampled_reading_count": len(measured), "limit": 128, "window_seconds": MAX_CURRENT_AGE_SECONDS, "hardware_and_calibration_verified": False}
    except Exception:
        return {"state": "unavailable", "candidate_device_count": None, "sampled_reading_count": None, "limit": 128, "window_seconds": MAX_CURRENT_AGE_SECONDS, "hardware_and_calibration_verified": False}


async def current_research_readiness() -> dict[str, Any]:
    readiness = base_readiness()
    readiness["fci_store"] = await stored_fci_evidence()
    return readiness
