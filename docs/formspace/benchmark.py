"""Reproduce bounded local contract benchmark, never cloud/DB/engine throughput."""
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mindex_api.formspace.contracts import Admission, canonical, digest

raw = (ROOT / "tests/fixtures/formspace/chain-request.json").read_bytes()
data = json.loads(raw)
def once():
    start = time.perf_counter()
    parsed = Admission.model_validate({"request": data}).model_dump()["request"]
    assert digest(canonical(parsed)) == digest(raw)
    return (time.perf_counter() - start) * 1000
cold = once()
durations = [once() for _ in range(500)]
peak_rss = None
try:
    import psutil
    info = psutil.Process().memory_info()
    peak_rss = getattr(info, "peak_wset", info.rss)
except ImportError:
    pass
print(json.dumps({"qualification": "local fixture contract parsing and canonical hashing only",
    "runtime": sys.version.split()[0], "os": platform.platform(), "cpu": platform.processor(),
    "logical_cpus": os.cpu_count(), "sample_count": len(data["dataset"]["samples"]),
    "request_bytes": len(raw), "cold_first_parse_ms": cold, "warm_iterations": 500,
    "warm_p50_ms": statistics.median(durations), "warm_p95_ms": sorted(durations)[474],
    "warm_operations_per_second": 1000 / statistics.mean(durations), "process_peak_rss_bytes": peak_rss,
    "database_network_aws_myca_measured": False}, indent=2))
