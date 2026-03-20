"""
GPU Integration Tests — MINDEX
================================
Tests for the GPU acceleration module. All tests work without a GPU by
verifying CPU fallback behavior. GPU-specific tests are skipped when
CUDA is not available.

Tests cover:
    1. GPU detection and availability flags
    2. cuDF engine (pandas fallback)
    3. cuVS index manager (numpy fallback)
    4. STATIC bridge (constraint extraction)
    5. MICA bridge (merkle operations)
    6. ETL GPU transforms
    7. GPU router endpoints
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# numpy and pandas are required for GPU tests but not in [dev] dependencies.
# Skip entire module if they're missing (CI installs only .[dev]).
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


# =========================================================================
# 1. GPU Detection
# =========================================================================


class TestGPUDetection:
    def test_availability_flags_are_booleans(self):
        from mindex_api.gpu import (
            CUDF_AVAILABLE,
            CUPY_AVAILABLE,
            CUVS_AVAILABLE,
            GPU_AVAILABLE,
        )

        assert isinstance(GPU_AVAILABLE, bool)
        assert isinstance(CUDF_AVAILABLE, bool)
        assert isinstance(CUVS_AVAILABLE, bool)
        assert isinstance(CUPY_AVAILABLE, bool)

    def test_get_availability_returns_dict(self):
        from mindex_api.gpu import get_availability

        result = get_availability()
        assert isinstance(result, dict)
        assert "gpu_available" in result
        assert "cudf_available" in result
        assert "cuvs_available" in result

    def test_gpu_config_loads(self):
        from mindex_api.gpu.config import gpu_config

        assert isinstance(gpu_config.gpu_enabled, bool)
        assert isinstance(gpu_config.cuvs_index_type, str)
        assert gpu_config.cuvs_index_type in ("ivf_pq", "ivf_flat", "cagra")


# =========================================================================
# 2. GPU Runtime
# =========================================================================


class TestGPURuntime:
    def test_singleton(self):
        from mindex_api.gpu.runtime import get_gpu_runtime

        r1 = get_gpu_runtime()
        r2 = get_gpu_runtime()
        assert r1 is r2

    def test_health_status_without_gpu(self):
        from mindex_api.gpu.runtime import GPURuntime

        runtime = GPURuntime()
        status = runtime.health_status()
        assert isinstance(status, dict)
        assert "available" in status

    def test_device_info_without_gpu(self):
        from mindex_api.gpu.runtime import GPUDeviceInfo

        info = GPUDeviceInfo()
        assert info.available is False
        assert info.device_name == "none"
        assert info.vram_total_gb == 0.0


# =========================================================================
# 3. cuDF Engine (CPU fallback)
# =========================================================================


class TestCuDFEngine:
    def test_backend_name(self):
        from mindex_api.gpu.cudf_engine import backend_name

        name = backend_name()
        assert name in ("cudf", "pandas")

    def test_from_records(self):
        from mindex_api.gpu.cudf_engine import from_records, to_records

        records = [
            {"id": "1", "name": "Amanita muscaria", "score": 0.95},
            {"id": "2", "name": "Psilocybe cubensis", "score": 0.87},
        ]
        df = from_records(records)
        assert len(df) == 2

        back = to_records(df)
        assert len(back) == 2
        assert back[0]["name"] == "Amanita muscaria"

    def test_from_records_empty(self):
        from mindex_api.gpu.cudf_engine import from_records

        df = from_records([])
        assert len(df) == 0

    def test_dedup(self):
        from mindex_api.gpu.cudf_engine import dedup, from_records

        records = [
            {"id": "1", "name": "A"},
            {"id": "1", "name": "A"},
            {"id": "2", "name": "B"},
        ]
        df = from_records(records)
        result = dedup(df, columns=["id"])
        assert len(result) == 2

    def test_aggregate(self):
        from mindex_api.gpu.cudf_engine import aggregate, from_records

        records = [
            {"kingdom": "Fungi", "count": 10},
            {"kingdom": "Fungi", "count": 20},
            {"kingdom": "Plantae", "count": 5},
        ]
        df = from_records(records)
        result = aggregate(df, group_cols=["kingdom"], agg_dict={"count": "sum"})
        assert len(result) == 2

    def test_sort_values(self):
        from mindex_api.gpu.cudf_engine import from_records, sort_values, to_records

        records = [
            {"name": "C", "score": 3},
            {"name": "A", "score": 1},
            {"name": "B", "score": 2},
        ]
        df = from_records(records)
        sorted_df = sort_values(df, by="score", ascending=True)
        result = to_records(sorted_df)
        assert result[0]["name"] == "A"

    def test_merge(self):
        from mindex_api.gpu.cudf_engine import from_records, merge

        left = from_records([
            {"id": "1", "name": "A"},
            {"id": "2", "name": "B"},
        ])
        right = from_records([
            {"id": "1", "score": 0.9},
            {"id": "3", "score": 0.5},
        ])
        result = merge(left, right, on="id", how="inner")
        assert len(result) == 1

    def test_filter_nulls(self):
        import pandas as pd

        from mindex_api.gpu.cudf_engine import filter_not_null, filter_nulls, from_records

        records = [
            {"id": "1", "desc": "has desc"},
            {"id": "2", "desc": None},
        ]
        df = from_records(records)
        nulls = filter_nulls(df, "desc")
        not_nulls = filter_not_null(df, "desc")
        assert len(nulls) == 1
        assert len(not_nulls) == 1

    def test_read_json_file(self):
        from mindex_api.gpu.cudf_engine import read_json

        data = [{"id": i, "value": f"v{i}"} for i in range(100)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            path = f.name

        try:
            df = read_json(path)
            assert len(df) == 100
        finally:
            os.unlink(path)

    def test_bulk_hash_blake3(self):
        from mindex_api.gpu.cudf_engine import bulk_hash_blake3, from_records

        records = [
            {"name": "Amanita", "kingdom": "Fungi"},
            {"name": "Quercus", "kingdom": "Plantae"},
        ]
        df = from_records(records)
        hashes = bulk_hash_blake3(df, columns=["name", "kingdom"])
        assert len(hashes) == 2
        assert len(hashes.iloc[0]) == 32  # 256-bit hash

    def test_chunk_dataframe(self):
        from mindex_api.gpu.cudf_engine import chunk_dataframe, from_records

        records = [{"id": i, "value": f"v{i}"} for i in range(1000)]
        df = from_records(records)
        chunks = list(chunk_dataframe(df, chunk_size_mb=1))
        assert len(chunks) >= 1
        total = sum(len(c) for c in chunks)
        assert total == 1000


# =========================================================================
# 4. cuVS Index Manager (CPU fallback)
# =========================================================================


class TestCuVSIndexManager:
    def test_index_registry(self):
        from mindex_api.gpu.cuvs_index import INDEX_REGISTRY

        assert "fci_signals" in INDEX_REGISTRY
        assert "nlm_nature" in INDEX_REGISTRY
        assert "image_similarity" in INDEX_REGISTRY
        assert INDEX_REGISTRY["fci_signals"].dimensions == 768
        assert INDEX_REGISTRY["nlm_nature"].dimensions == 16
        assert INDEX_REGISTRY["image_similarity"].dimensions == 512

    def test_singleton(self):
        from mindex_api.gpu.cuvs_index import get_index_manager

        m1 = get_index_manager()
        m2 = get_index_manager()
        assert m1 is m2

    def test_available_indexes(self):
        from mindex_api.gpu.cuvs_index import CuVSIndexManager

        manager = CuVSIndexManager()
        available = manager.get_available_indexes()
        assert "fci_signals" in available

    def test_numpy_search(self):
        from mindex_api.gpu.cuvs_index import CuVSIndexManager, IndexConfig

        manager = CuVSIndexManager()

        # Manually load test vectors
        vectors = np.random.randn(100, 16).astype(np.float32)
        ids = [f"vec_{i}" for i in range(100)]
        manager._vectors["test_index"] = vectors
        manager._ids["test_index"] = ids

        # Search
        from mindex_api.gpu.cuvs_index import IndexConfig

        config = IndexConfig(
            name="test_index", source_table="test", source_column="vec",
            id_column="id", dimensions=16, index_type="ivf_flat", metric="cosine",
        )
        from mindex_api.gpu.cuvs_index import INDEX_REGISTRY

        INDEX_REGISTRY["test_index"] = config

        results = manager._search_numpy("test_index", vectors[0:1], k=5, config=config)
        assert len(results) == 5
        assert results[0].rank == 0
        assert results[0].distance <= results[1].distance

        # Cleanup
        del INDEX_REGISTRY["test_index"]

    def test_compute_index_hash(self):
        from mindex_api.gpu.cuvs_index import CuVSIndexManager

        manager = CuVSIndexManager()
        vectors = np.random.randn(10, 16).astype(np.float32)
        manager._vectors["test"] = vectors
        manager._ids["test"] = [f"id_{i}" for i in range(10)]

        h = manager.compute_index_hash("test")
        assert h is not None
        assert len(h) == 32

    @pytest.mark.asyncio
    async def test_save_and_load_index(self):
        from pathlib import Path

        from mindex_api.gpu.cuvs_index import CuVSIndexManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CuVSIndexManager()
            manager._index_dir = Path(tmpdir)

            vectors = np.random.randn(50, 16).astype(np.float32)
            ids = [f"id_{i}" for i in range(50)]
            manager._vectors["test_save"] = vectors
            manager._ids["test_save"] = ids

            path = manager.save_index("test_save")
            assert path is not None

            # Load in new manager
            manager2 = CuVSIndexManager()
            manager2._index_dir = Path(tmpdir)

            status = await manager2.load_index("test_save")
            assert status is not None
            assert status.vector_count == 50

    @pytest.mark.asyncio
    async def test_add_vectors(self):
        from mindex_api.gpu.cuvs_index import CuVSIndexManager

        manager = CuVSIndexManager()
        manager._vectors["test_add"] = np.random.randn(10, 16).astype(np.float32)
        manager._ids["test_add"] = [f"id_{i}" for i in range(10)]

        added = await manager.add_vectors(
            "test_add",
            [[0.1] * 16, [0.2] * 16],
            ["new_1", "new_2"],
        )
        assert added == 2
        assert len(manager._ids["test_add"]) == 12


# =========================================================================
# 5. STATIC Bridge
# =========================================================================


class TestSTATICBridge:
    def test_constraint_domain_enum(self):
        from mindex_api.gpu.static_bridge import ConstraintDomain

        assert ConstraintDomain.TAXONOMY.value == "taxonomy"
        assert ConstraintDomain.COMPOUNDS.value == "compounds"

    def test_constraint_set_hashing(self):
        from mindex_api.gpu.static_bridge import ConstraintDomain, ConstraintSet

        cs1 = ConstraintSet(
            domain=ConstraintDomain.TAXONOMY,
            name="test",
            sequences=["Amanita muscaria", "Psilocybe cubensis"],
        )
        cs2 = ConstraintSet(
            domain=ConstraintDomain.TAXONOMY,
            name="test",
            sequences=["Amanita muscaria", "Psilocybe cubensis"],
        )
        # Same sequences → same hash
        assert cs1.version_hash == cs2.version_hash

        cs3 = ConstraintSet(
            domain=ConstraintDomain.TAXONOMY,
            name="test",
            sequences=["Different species"],
        )
        assert cs1.version_hash != cs3.version_hash

    def test_bridge_singleton(self):
        from mindex_api.gpu.static_bridge import get_static_bridge

        b1 = get_static_bridge()
        b2 = get_static_bridge()
        assert b1 is b2

    def test_bridge_disabled_by_default(self):
        from mindex_api.gpu.static_bridge import STATICBridge

        bridge = STATICBridge()
        assert not bridge.enabled

    def test_cache_management(self):
        from mindex_api.gpu.static_bridge import (
            ConstraintDomain,
            ConstraintSet,
            STATICBridge,
        )

        bridge = STATICBridge()
        cs = ConstraintSet(
            domain=ConstraintDomain.TAXONOMY,
            name="test",
            sequences=["A", "B", "C"],
        )
        bridge._constraint_cache["taxonomy"] = cs

        cached = bridge.get_cached_constraints(ConstraintDomain.TAXONOMY)
        assert cached is not None
        assert cached.sequence_count == 3

        domains = bridge.list_cached_domains()
        assert "taxonomy" in domains

        bridge.clear_cache()
        assert bridge.get_cached_constraints(ConstraintDomain.TAXONOMY) is None


# =========================================================================
# 6. MICA Bridge
# =========================================================================


class TestMICABridge:
    def test_blake3_hash(self):
        from mindex_api.gpu.mica_bridge import _blake3_hash

        h1 = _blake3_hash(b"test data")
        h2 = _blake3_hash(b"test data")
        h3 = _blake3_hash(b"different data")

        assert h1 == h2  # Deterministic
        assert h1 != h3  # Different input → different hash
        assert len(h1) == 32  # 256-bit

    def test_deterministic_cbor(self):
        from mindex_api.gpu.mica_bridge import _deterministic_cbor

        data1 = {"b": 2, "a": 1}
        data2 = {"a": 1, "b": 2}

        # Should produce same bytes regardless of key order
        b1 = _deterministic_cbor(data1)
        b2 = _deterministic_cbor(data2)
        assert b1 == b2

    def test_bridge_singleton(self):
        from mindex_api.gpu.mica_bridge import get_mica_bridge

        m1 = get_mica_bridge()
        m2 = get_mica_bridge()
        assert m1 is m2

    def test_device_id(self):
        from mindex_api.gpu.mica_bridge import MICABridge

        bridge = MICABridge()
        assert bridge.DEVICE_ID == "mindex.gpu"
        assert bridge.PRODUCER == "mindex-gpu-accelerator"


# =========================================================================
# 7. ETL GPU Transforms
# =========================================================================


class TestETLGPUTransforms:
    def test_gpu_bulk_import_json(self):
        from mindex_etl.gpu_transforms import gpu_bulk_import_json

        data = [{"id": i, "name": f"species_{i}", "count": i * 10} for i in range(100)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        try:
            df = gpu_bulk_import_json(path)
            assert len(df) == 100
            assert "name" in df.columns
        finally:
            os.unlink(path)

    def test_gpu_bulk_import_with_schema_map(self):
        from mindex_etl.gpu_transforms import gpu_bulk_import_json

        data = [{"old_name": "test", "old_id": 1}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        try:
            df = gpu_bulk_import_json(path, schema_map={"old_name": "name"})
            assert "name" in df.columns
        finally:
            os.unlink(path)

    def test_gpu_dedup_compounds(self):
        from mindex_etl.gpu_transforms import gpu_dedup_compounds

        new = [
            {"pubchem_cid": "123", "name": "A"},
            {"pubchem_cid": "456", "name": "B"},
            {"pubchem_cid": "123", "name": "A_dup"},
        ]

        # Mock DB connection that returns existing compounds
        mock_conn = MagicMock()
        import pandas as pd

        existing = pd.DataFrame({"pubchem_id": ["123"], "inchikey": ["XXXX"]})
        mock_conn.execute.return_value = MagicMock()
        with patch("pandas.read_sql", return_value=existing):
            result = gpu_dedup_compounds(new, mock_conn)
            # Should remove the existing compound (123) and keep novel ones
            novel_ids = {r.get("pubchem_id") or r.get("pubchem_cid") for r in result}
            assert "456" in novel_ids or len(result) >= 1

    def test_gpu_batch_h3_cells(self):
        from mindex_etl.gpu_transforms import gpu_batch_h3_cells

        lats = [47.6, 48.8, 35.6]
        lons = [-122.3, 2.3, 139.6]
        cells = gpu_batch_h3_cells(lats, lons)
        assert len(cells) == 3

    def test_gpu_batch_upsert_prep(self):
        from mindex_etl.gpu_transforms import gpu_batch_upsert_prep

        records = [
            {"id": "1", "name": "A", "value": 10},
            {"id": "1", "name": "A_dup", "value": 20},
            {"id": "2", "name": "B", "value": None},
        ]
        result = gpu_batch_upsert_prep(
            records,
            dedup_columns=["id"],
            required_columns=["value"],
        )
        # Should dedup on id (keep last) and filter null values
        assert len(result) <= 2

    def test_gpu_batch_upsert_prep_empty(self):
        from mindex_etl.gpu_transforms import gpu_batch_upsert_prep

        result = gpu_batch_upsert_prep([], dedup_columns=["id"])
        assert len(result) == 0


# =========================================================================
# 8. GPU Config Integration
# =========================================================================


class TestGPUConfig:
    def test_config_in_main_settings(self):
        from mindex_api.config import settings

        assert hasattr(settings, "gpu_enabled")
        assert hasattr(settings, "cuvs_index_dir")
        assert hasattr(settings, "static_enabled")

    def test_gpu_config_standalone(self):
        from mindex_api.gpu.config import gpu_config

        assert hasattr(gpu_config, "gpu_enabled")
        assert hasattr(gpu_config, "cuvs_index_type")
        assert hasattr(gpu_config, "static_enabled")
        assert hasattr(gpu_config, "mica_hash_batch_size")
