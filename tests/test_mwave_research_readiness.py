"""Offline contract qualification: no model, hardware or production inference is implied."""
import asyncio
import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mindex_api import mwave_research as research

# Load the actual router without importing unrelated optional router integrations.
spec = importlib.util.spec_from_file_location("mindex_api.routers.mwave", Path(__file__).parents[1] / "mindex_api/routers/mwave.py")
mwave = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mwave)

NOW = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


def window(**updates):
    value = {"device_id": "declared-device", "channel_id": "electrode-1",
        "source_kind": "hardware-measurement", "source_record_id": "fixture-record",
        "source_sha256": "a" * 64, "calibration_record_id": "declared-calibration",
        "measured_at": (NOW - timedelta(seconds=20)).isoformat(), "sample_rate_hz": 128,
        "units": "uV", "values": [1.5, 2.5, 3.5], "latitude": 32.7, "longitude": -117.1,
        "clock_uncertainty_ms": 1, "snr_db": 12, "quality_score": 0.8}
    value.update(updates)
    return value


def batch(windows, mode="research-shadow", **updates):
    return research.ResearchInputBatch.model_validate({"mode": mode, "windows": windows,
        "analysis_cutoff": NOW.isoformat(), **updates})


class InputContractTests(unittest.TestCase):
    def test_valid_declared_input_does_not_qualify_device_model_prediction_or_persistence(self):
        result = research.evaluate_research_inputs(batch([window()]), NOW)
        self.assertEqual(result["state"], "input-schema-valid")
        self.assertEqual(result["declared_device_count"], 1)
        self.assertIsNone(result["current_sensor_count"])
        self.assertFalse(result["hardware_validated"])
        self.assertFalse(result["prediction_available"])
        self.assertFalse(result["public_alerts_enabled"])
        self.assertFalse(result["persisted"])
        self.assertEqual(result["model"]["state"], "unbound")
        self.assertEqual(result["replay"]["state"], "unavailable")

    def test_stale_simulated_low_quality_and_future_cutoff_leakage_are_rejected(self):
        examples = [window(measured_at=(NOW - timedelta(hours=2)).isoformat()),
            window(source_kind="simulation"), window(quality_score=0.1),
            window(measured_at=NOW.isoformat(), values=[1, 2], sample_rate_hz=1)]
        result = research.evaluate_research_inputs(batch(examples), NOW)
        self.assertEqual(result["accepted_windows"], [])
        self.assertEqual(len(result["rejected_windows"]), 4)
        self.assertEqual(result["state"], "unavailable")

    def test_archive_input_is_only_explicit_replay_and_not_a_retained_evaluation(self):
        archived = window(source_kind="archived-measurement", measured_at="2025-01-01T00:00:00Z")
        self.assertEqual(research.evaluate_research_inputs(batch([archived]), NOW)["state"], "unavailable")
        replay = research.evaluate_research_inputs(batch([archived], "research-replay"), NOW)
        self.assertEqual(replay["state"], "input-schema-valid")
        self.assertEqual(replay["mode"], "research-replay")
        self.assertEqual(replay["replay"]["state"], "unavailable")
        self.assertFalse(replay["prediction_available"])

    def test_schema_rejects_nonfinite_naive_dates_bad_coords_units_and_budgets(self):
        for update in ({"values": [1, float("nan")]}, {"measured_at": "2026-10-09T20:00:00"},
            {"latitude": 91}, {"sample_rate_hz": 0}, {"units": "arbitrary"}, {"source_sha256": "invalid"}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                batch([window(**update)])
        with self.assertRaises(ValidationError):
            batch([window(values=[1] * 4096) for _ in range(5)])
        with self.assertRaises(ValidationError):
            batch([window()] * 65)

    def test_fci_read_uses_readonly_transaction_time_and_row_budgets_without_claiming_calibration(self):
        evidence = {}
        class Context:
            def __init__(self, value): self.value = value
            async def __aenter__(self): return self.value
            async def __aexit__(self, *args): return None
        class Connection:
            def transaction(self, **kwargs): evidence["transaction"] = kwargs; return Context(self)
            async def execute(self, query): evidence["timeout"] = query
            async def fetch(self, query):
                evidence["query"] = query
                return [{"amplitude_uv": 0.0, "device_id": "A"}, {"amplitude_uv": 2.5, "device_id": "A"},
                    {"amplitude_uv": float("nan"), "device_id": "B"}]
        class Pool:
            def acquire(self): return Context(Connection())
        async def pool(): return Pool()
        with patch.dict(sys.modules, {"mindex_api.db": types.SimpleNamespace(get_db_pool=pool)}):
            result = asyncio.run(research.stored_fci_evidence())
        self.assertEqual(evidence["transaction"], {"readonly": True})
        self.assertIn("2000ms", evidence["timeout"])
        self.assertIn("LIMIT 128", evidence["query"])
        self.assertIn("INTERVAL '10 minutes'", evidence["query"])
        self.assertEqual(result["candidate_device_count"], 1)
        self.assertEqual(result["sampled_reading_count"], 2)
        self.assertFalse(result["hardware_and_calibration_verified"])

    def test_store_failure_is_unavailable_unknown_count_not_no_sensors(self):
        async def broken(): raise RuntimeError("DO_NOT_EXPOSE_DATABASE_PASSWORD")
        with patch.dict(sys.modules, {"mindex_api.db": types.SimpleNamespace(get_db_pool=broken)}):
            result = asyncio.run(research.current_research_readiness())
        self.assertEqual(result["fci_store"]["state"], "unavailable")
        self.assertIsNone(result["fci_store"]["candidate_device_count"])
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result))


class RouterTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(mwave.router, prefix="/api/mindex")
        self.client = TestClient(app)

    def test_real_schema_and_evaluation_routes_bound_and_redact_payload_errors(self):
        schema = self.client.get("/api/mindex/mwave/input-schema")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("BioelectricWindow", schema.json()["schema"]["$defs"])
        self.assertFalse(schema.json()["prediction_available"])
        result = self.client.post("/api/mindex/mwave/readiness/evaluate", json={"mode": "research-replay",
            "analysis_cutoff": NOW.isoformat(), "windows": [window(source_kind="archived-measurement")]})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["prediction_available"])
        invalid = self.client.post("/api/mindex/mwave/readiness/evaluate", json={"secret": "DO_NOT_ECHO_SENSOR_DATA"})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("DO_NOT_ECHO", invalid.text)
        oversized = self.client.post("/api/mindex/mwave/readiness/evaluate", content=b" " * (mwave.MAX_INPUT_BYTES + 1))
        self.assertEqual(oversized.status_code, 413)

    def test_partial_catalog_preserves_actual_records_missing_coordinates_and_unknown_day(self):
        event = mwave.measured_event({"id": "usgs-actual", "properties": {"mag": 2.5, "time": 1000,
            "place": "Reported place", "url": "https://earthquake.usgs.gov/earthquakes/eventpage/usgs-actual"}})
        async def feed(client, period): return {"events": [event], "generated_at": "2026-10-09T20:00:00Z"} if period == "hour" else None
        with patch.object(mwave, "fetch_catalog", feed):
            response = self.client.get("/api/mindex/mwave")
        body = response.json()
        self.assertEqual(body["status"], "partial")
        self.assertEqual(body["earthquakes"]["count_hour"], 1)
        self.assertIsNone(body["earthquakes"]["count_day"])
        self.assertIsNone(body["earthquakes"]["max_magnitude_24h"])
        self.assertIsNone(body["earthquakes"]["hour"][0]["latitude"])
        self.assertFalse(body["prediction_available"])
        self.assertIsNone(body["sensor_count"])

    def test_unavailable_catalog_is_not_zero_all_clear(self):
        async def missing(client, period): return None
        with patch.object(mwave, "fetch_catalog", missing):
            body = self.client.get("/api/mindex/mwave").json()
        self.assertEqual(body["status"], "offline")
        self.assertEqual(body["measured_seismic"]["state"], "unavailable")
        self.assertIsNone(body["earthquakes"]["count_hour"])
        self.assertIsNone(body["prediction_confidence"])

    def test_catalog_loader_checks_http_schema_and_stream_budget(self):
        payload = {"metadata": {"generated": 1791576000000}, "features": [{"id": "real-id",
            "properties": {"mag": 2.0}, "geometry": {"coordinates": [-117, 32, 3]}}]}
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
                result = await mwave.fetch_catalog(client, "hour")
                self.assertEqual(result["events"][0]["latitude"], 32)
                self.assertIsNotNone(result["generated_at"])
                with patch.object(mwave, "MAX_SOURCE_BYTES", 1):
                    self.assertIsNone(await mwave.fetch_catalog(client, "hour"))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
