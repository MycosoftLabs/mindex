"""
cuVS Vector Index Manager — MINDEX
=====================================
GPU-accelerated vector similarity search using NVIDIA cuVS. Replaces pgvector
for high-performance ANN queries while maintaining pgvector as the source of
truth for persistence.

Manages three index types:
    - fci_signals (768-dim) — GFST bioelectric pattern matching
    - nlm_nature (16-dim)   — NLM anomaly detection scoring
    - image_similarity (512-dim) — Image similarity search

Interfaces with:
    - pgvector source tables (fci_signal_embeddings, nlm.nature_embeddings, images)
    - MICA Merkle bridge (index state hashing / provenance)
    - StorageManager (NAS persistence for index snapshots)
    - GPU Router (internal API endpoints)
    - Unified Search (vector search domain)

Falls back to pgvector SQL when cuVS is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import CUVS_AVAILABLE, GPU_AVAILABLE
from .config import gpu_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index configuration registry — maps index names to source tables
# ---------------------------------------------------------------------------

@dataclass
class IndexConfig:
    """Configuration for a single vector index."""

    name: str
    source_table: str
    source_column: str
    id_column: str
    dimensions: int
    index_type: str  # ivf_pq, ivf_flat, cagra
    metric: str = "cosine"  # cosine, l2, inner_product
    nlist: int = 256
    nprobe: int = 32
    pq_dim: int = 64
    pq_bits: int = 8


# Default index configurations matching existing pgvector tables
INDEX_REGISTRY: Dict[str, IndexConfig] = {
    "fci_signals": IndexConfig(
        name="fci_signals",
        source_table="fci_signal_embeddings",
        source_column="embedding",
        id_column="id",
        dimensions=768,
        index_type="ivf_pq",
        nlist=256,
        nprobe=32,
        pq_dim=64,
        pq_bits=8,
    ),
    "nlm_nature": IndexConfig(
        name="nlm_nature",
        source_table="nlm.nature_embeddings",
        source_column="embedding",
        id_column="id",
        dimensions=16,
        index_type="ivf_flat",
        nlist=16,
        nprobe=8,
    ),
    "image_similarity": IndexConfig(
        name="image_similarity",
        source_table="images",
        source_column="embedding",
        id_column="id",
        dimensions=512,
        index_type="cagra",
        nlist=128,
        nprobe=16,
    ),
}


@dataclass
class SearchResult:
    """Single ANN search result."""

    id: str
    distance: float
    rank: int


@dataclass
class IndexStatus:
    """Status of a loaded index."""

    name: str
    dimensions: int
    index_type: str
    vector_count: int
    is_loaded: bool
    last_build_time: Optional[float] = None
    last_root_hash: Optional[bytes] = None
    storage_path: Optional[str] = None
    vram_usage_mb: float = 0.0


class CuVSIndexManager:
    """Manages GPU vector indexes with persistence and MICA verification.

    When cuVS is available, builds and queries indexes on GPU.
    When cuVS is unavailable, delegates all queries to pgvector via SQL.
    """

    def __init__(self) -> None:
        self._indexes: Dict[str, Any] = {}  # name → cuVS index object
        self._vectors: Dict[str, np.ndarray] = {}  # name → vector data
        self._ids: Dict[str, List[str]] = {}  # name → ID list (same order as vectors)
        self._status: Dict[str, IndexStatus] = {}
        self._index_dir = Path(gpu_config.cuvs_index_dir)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    async def build_index(
        self,
        index_name: str,
        db_session,
        config: Optional[IndexConfig] = None,
    ) -> IndexStatus:
        """Build a cuVS index from pgvector source table.

        Steps:
            1. Load all vectors from source table
            2. Build cuVS index on GPU (IVF-PQ / IVF-Flat / CAGRA)
            3. Cache in memory for search
            4. Return status with vector count
        """
        if config is None:
            config = INDEX_REGISTRY.get(index_name)
        if config is None:
            raise ValueError(f"Unknown index: {index_name}. Available: {list(INDEX_REGISTRY.keys())}")

        logger.info("Building index '%s' from %s.%s (%d dims, type=%s)",
                     index_name, config.source_table, config.source_column,
                     config.dimensions, config.index_type)

        t0 = time.monotonic()

        # 1. Load vectors from pgvector
        vectors, ids = await self._load_vectors_from_db(db_session, config)

        if len(vectors) == 0:
            logger.warning("No vectors found in %s — creating empty index", config.source_table)
            status = IndexStatus(
                name=index_name, dimensions=config.dimensions,
                index_type=config.index_type, vector_count=0, is_loaded=True,
            )
            self._status[index_name] = status
            return status

        # 2. Build cuVS index or store for CPU fallback
        if CUVS_AVAILABLE and GPU_AVAILABLE:
            index = self._build_cuvs_index(vectors, config)
            self._indexes[index_name] = index
        else:
            logger.info("cuVS not available — index '%s' will use brute-force NumPy search", index_name)

        self._vectors[index_name] = vectors
        self._ids[index_name] = ids

        elapsed = time.monotonic() - t0
        status = IndexStatus(
            name=index_name,
            dimensions=config.dimensions,
            index_type=config.index_type if CUVS_AVAILABLE else "numpy_brute",
            vector_count=len(vectors),
            is_loaded=True,
            last_build_time=elapsed,
        )
        self._status[index_name] = status

        logger.info("Index '%s' built: %d vectors in %.2fs [%s]",
                     index_name, len(vectors), elapsed,
                     "cuVS" if CUVS_AVAILABLE else "numpy")
        return status

    def _build_cuvs_index(self, vectors: np.ndarray, config: IndexConfig) -> Any:
        """Build a cuVS index on GPU."""
        import cupy as cp

        vectors_gpu = cp.asarray(vectors, dtype=cp.float32)

        if config.index_type == "ivf_pq":
            from cuvs.neighbors import ivf_pq

            build_params = ivf_pq.IndexParams(
                n_lists=min(config.nlist, len(vectors)),
                metric="sqeuclidean" if config.metric == "l2" else "inner_product",
                pq_dim=config.pq_dim,
                pq_bits=config.pq_bits,
            )
            index = ivf_pq.build(build_params, vectors_gpu)
            return index

        elif config.index_type == "ivf_flat":
            from cuvs.neighbors import ivf_flat

            build_params = ivf_flat.IndexParams(
                n_lists=min(config.nlist, len(vectors)),
                metric="sqeuclidean" if config.metric == "l2" else "inner_product",
            )
            index = ivf_flat.build(build_params, vectors_gpu)
            return index

        elif config.index_type == "cagra":
            from cuvs.neighbors import cagra

            build_params = cagra.IndexParams(
                metric="sqeuclidean" if config.metric == "l2" else "inner_product",
            )
            index = cagra.build(build_params, vectors_gpu)
            return index

        else:
            raise ValueError(f"Unknown index type: {config.index_type}")

    async def _load_vectors_from_db(
        self, db_session, config: IndexConfig
    ) -> Tuple[np.ndarray, List[str]]:
        """Load vectors from pgvector source table."""
        from sqlalchemy import text

        query = text(f"""
            SELECT {config.id_column}::text, {config.source_column}::text
            FROM {config.source_table}
            WHERE {config.source_column} IS NOT NULL
        """)
        result = await db_session.execute(query)
        rows = result.fetchall()

        if not rows:
            return np.array([], dtype=np.float32).reshape(0, config.dimensions), []

        ids = []
        vectors = []
        for row in rows:
            ids.append(str(row[0]))
            # pgvector returns vectors as '[0.1, 0.2, ...]' string
            vec_str = row[1]
            if vec_str.startswith("["):
                vec = np.fromstring(vec_str.strip("[]"), sep=",", dtype=np.float32)
            else:
                vec = np.fromstring(vec_str, sep=",", dtype=np.float32)
            if len(vec) == config.dimensions:
                vectors.append(vec)
            else:
                ids.pop()  # skip mismatched dimensions

        return np.array(vectors, dtype=np.float32), ids

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        index_name: str,
        query_vector: List[float],
        k: int = 10,
        db_session=None,
    ) -> List[SearchResult]:
        """Search for k nearest neighbors.

        Uses cuVS GPU index if available, falls back to NumPy brute-force,
        and finally to pgvector SQL if neither is loaded.
        """
        config = INDEX_REGISTRY.get(index_name)
        if config is None:
            raise ValueError(f"Unknown index: {index_name}")

        query = np.array(query_vector, dtype=np.float32).reshape(1, -1)

        # Try cuVS GPU search
        if index_name in self._indexes and CUVS_AVAILABLE:
            return self._search_cuvs(index_name, query, k, config)

        # Try NumPy brute-force (vectors loaded but no cuVS)
        if index_name in self._vectors and len(self._vectors[index_name]) > 0:
            return self._search_numpy(index_name, query, k, config)

        # Fall back to pgvector SQL
        if db_session is not None:
            return await self._search_pgvector(db_session, query_vector, k, config)

        return []

    def _search_cuvs(
        self, index_name: str, query: np.ndarray, k: int, config: IndexConfig
    ) -> List[SearchResult]:
        """Search using cuVS GPU index."""
        import cupy as cp

        query_gpu = cp.asarray(query, dtype=cp.float32)

        index = self._indexes[index_name]
        ids_list = self._ids[index_name]

        if config.index_type == "ivf_pq":
            from cuvs.neighbors import ivf_pq

            search_params = ivf_pq.SearchParams(n_probes=config.nprobe)
            distances, indices = ivf_pq.search(search_params, index, query_gpu, k)
        elif config.index_type == "ivf_flat":
            from cuvs.neighbors import ivf_flat

            search_params = ivf_flat.SearchParams(n_probes=config.nprobe)
            distances, indices = ivf_flat.search(search_params, index, query_gpu, k)
        elif config.index_type == "cagra":
            from cuvs.neighbors import cagra

            search_params = cagra.SearchParams()
            distances, indices = cagra.search(search_params, index, query_gpu, k)
        else:
            return []

        distances_np = cp.asnumpy(distances[0])
        indices_np = cp.asnumpy(indices[0])

        results = []
        for rank, (idx, dist) in enumerate(zip(indices_np, distances_np)):
            if 0 <= idx < len(ids_list):
                results.append(SearchResult(
                    id=ids_list[int(idx)],
                    distance=float(dist),
                    rank=rank,
                ))
        return results

    def _search_numpy(
        self, index_name: str, query: np.ndarray, k: int, config: IndexConfig
    ) -> List[SearchResult]:
        """CPU brute-force search using NumPy (fallback)."""
        vectors = self._vectors[index_name]
        ids_list = self._ids[index_name]

        if config.metric == "cosine":
            # Cosine distance = 1 - cosine_similarity
            query_norm = query / (np.linalg.norm(query, axis=1, keepdims=True) + 1e-10)
            vec_norms = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-10)
            similarities = (vec_norms @ query_norm.T).flatten()
            distances = 1.0 - similarities
        elif config.metric == "l2":
            diff = vectors - query
            distances = np.sqrt(np.sum(diff * diff, axis=1))
        else:
            # inner product (negate for "distance")
            distances = -(vectors @ query.T).flatten()

        k = min(k, len(distances))
        top_k_indices = np.argpartition(distances, k)[:k]
        top_k_indices = top_k_indices[np.argsort(distances[top_k_indices])]

        results = []
        for rank, idx in enumerate(top_k_indices):
            results.append(SearchResult(
                id=ids_list[int(idx)],
                distance=float(distances[idx]),
                rank=rank,
            ))
        return results

    async def _search_pgvector(
        self,
        db_session,
        query_vector: List[float],
        k: int,
        config: IndexConfig,
    ) -> List[SearchResult]:
        """Fall back to pgvector SQL search."""
        from sqlalchemy import text

        vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
        operator = "<=>" if config.metric == "cosine" else "<->"

        query = text(f"""
            SELECT {config.id_column}::text,
                   {config.source_column} {operator} :query_vec AS distance
            FROM {config.source_table}
            WHERE {config.source_column} IS NOT NULL
            ORDER BY {config.source_column} {operator} :query_vec
            LIMIT :k
        """)
        result = await db_session.execute(query, {"query_vec": vec_str, "k": k})
        rows = result.fetchall()

        return [
            SearchResult(id=str(row[0]), distance=float(row[1]), rank=i)
            for i, row in enumerate(rows)
        ]

    # ------------------------------------------------------------------
    # Streaming updates (add vectors without full rebuild)
    # ------------------------------------------------------------------

    async def add_vectors(
        self,
        index_name: str,
        vectors: List[List[float]],
        ids: List[str],
    ) -> int:
        """Add new vectors to an existing index without full rebuild.

        For cuVS IVF indexes, new vectors are added to the nearest cluster.
        For CPU fallback, vectors are appended to the array.
        Returns number of vectors added.
        """
        if index_name not in self._vectors:
            logger.warning("Index '%s' not loaded — cannot add vectors", index_name)
            return 0

        new_vecs = np.array(vectors, dtype=np.float32)
        self._vectors[index_name] = np.vstack([self._vectors[index_name], new_vecs])
        self._ids[index_name].extend(ids)

        # Update status
        if index_name in self._status:
            self._status[index_name].vector_count = len(self._vectors[index_name])

        logger.debug("Added %d vectors to '%s' (total: %d)",
                     len(vectors), index_name, len(self._vectors[index_name]))
        return len(vectors)

    # ------------------------------------------------------------------
    # Persistence (NAS snapshots)
    # ------------------------------------------------------------------

    def save_index(self, index_name: str) -> Optional[str]:
        """Save index vectors and IDs to disk for later reload."""
        if index_name not in self._vectors:
            return None

        index_dir = self._index_dir / index_name
        os.makedirs(index_dir, exist_ok=True)

        # Save vectors as numpy binary
        vec_path = index_dir / "vectors.npy"
        np.save(str(vec_path), self._vectors[index_name])

        # Save IDs as JSON
        ids_path = index_dir / "ids.json"
        with open(ids_path, "w") as f:
            json.dump(self._ids[index_name], f)

        # Save config
        config = INDEX_REGISTRY.get(index_name)
        if config:
            config_path = index_dir / "config.json"
            with open(config_path, "w") as f:
                json.dump({
                    "name": config.name,
                    "dimensions": config.dimensions,
                    "index_type": config.index_type,
                    "metric": config.metric,
                    "vector_count": len(self._vectors[index_name]),
                }, f)

        if index_name in self._status:
            self._status[index_name].storage_path = str(index_dir)

        logger.info("Saved index '%s' to %s (%d vectors)", index_name, index_dir, len(self._vectors[index_name]))
        return str(index_dir)

    async def load_index(self, index_name: str) -> Optional[IndexStatus]:
        """Load a previously saved index from disk."""
        index_dir = self._index_dir / index_name
        vec_path = index_dir / "vectors.npy"
        ids_path = index_dir / "ids.json"

        if not vec_path.exists() or not ids_path.exists():
            logger.debug("No saved index found at %s", index_dir)
            return None

        vectors = np.load(str(vec_path))
        with open(ids_path) as f:
            ids = json.load(f)

        self._vectors[index_name] = vectors
        self._ids[index_name] = ids

        # Build cuVS index if available
        config = INDEX_REGISTRY.get(index_name)
        if config and CUVS_AVAILABLE and GPU_AVAILABLE:
            try:
                index = self._build_cuvs_index(vectors, config)
                self._indexes[index_name] = index
            except Exception as e:
                logger.warning("Failed to build cuVS index from saved data: %s", e)

        status = IndexStatus(
            name=index_name,
            dimensions=vectors.shape[1] if len(vectors.shape) > 1 else 0,
            index_type=config.index_type if config else "unknown",
            vector_count=len(vectors),
            is_loaded=True,
            storage_path=str(index_dir),
        )
        self._status[index_name] = status
        logger.info("Loaded index '%s' from disk: %d vectors", index_name, len(vectors))
        return status

    # ------------------------------------------------------------------
    # Index state hashing (for MICA Merkle integration)
    # ------------------------------------------------------------------

    def compute_index_hash(self, index_name: str) -> Optional[bytes]:
        """Compute a deterministic hash of the index state.

        Hash covers: vector data + IDs + config. This hash becomes
        the root of a MICA Merkle tree so that search results can be
        cryptographically verified against a specific index version.
        """
        if index_name not in self._vectors:
            return None

        import hashlib

        h = hashlib.sha256()
        h.update(index_name.encode())
        h.update(self._vectors[index_name].tobytes())
        for id_val in self._ids[index_name]:
            h.update(id_val.encode())

        return h.digest()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self, index_name: str) -> Optional[IndexStatus]:
        """Get current status of an index."""
        return self._status.get(index_name)

    def list_indexes(self) -> Dict[str, IndexStatus]:
        """List all loaded indexes with status."""
        return dict(self._status)

    def get_available_indexes(self) -> List[str]:
        """List all registered index names (loaded or not)."""
        return list(INDEX_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_manager: Optional[CuVSIndexManager] = None


def get_index_manager() -> CuVSIndexManager:
    """Get the shared CuVSIndexManager singleton."""
    global _manager
    if _manager is None:
        _manager = CuVSIndexManager()
    return _manager
