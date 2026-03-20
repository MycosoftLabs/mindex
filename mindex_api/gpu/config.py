"""
GPU Configuration — MINDEX
============================
Pydantic settings for GPU acceleration. All settings are optional and
default to safe values when GPU hardware is not present.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class GPUConfig(BaseSettings):
    """GPU acceleration configuration.

    All values can be overridden via environment variables prefixed with GPU_
    or set directly in .env.
    """

    # Master switch — must be True AND hardware must be present
    gpu_enabled: bool = Field(
        False,
        description="Enable GPU acceleration (requires CUDA + RAPIDS). "
        "Even when True, gracefully falls back to CPU if hardware is unavailable.",
    )

    # Device selection
    gpu_device_id: int = Field(0, description="CUDA device ordinal to use.")

    # Memory management
    gpu_memory_limit_gb: float = Field(
        0.0,
        description="Max GPU memory to use in GB (0 = auto, uses 80%% of available VRAM).",
    )
    gpu_memory_pool_type: str = Field(
        "managed",
        description="RMM pool type: 'managed' (UVM), 'pool' (device-only), 'cuda' (default allocator).",
    )

    # cuDF settings
    cudf_spill_dir: str = Field(
        "/tmp/cudf_spill",
        description="Directory for cuDF memory spill when GPU VRAM is full.",
    )
    cudf_chunk_size_mb: int = Field(
        512,
        description="Default chunk size in MB for GPU DataFrame operations.",
    )

    # cuVS vector index settings
    cuvs_index_type: str = Field(
        "ivf_pq",
        description="Default cuVS index type: 'ivf_pq', 'ivf_flat', or 'cagra'.",
    )
    cuvs_index_dir: str = Field(
        "/data/cuvs_indexes",
        description="Persistent storage directory for cuVS indexes.",
    )
    cuvs_nlist: int = Field(
        256,
        description="Number of IVF clusters for index building.",
    )
    cuvs_nprobe: int = Field(
        32,
        description="Number of clusters to probe during search.",
    )
    cuvs_pq_dim: int = Field(
        64,
        description="PQ sub-vector dimension for IVF-PQ indexes.",
    )
    cuvs_pq_bits: int = Field(
        8,
        description="PQ encoding bits (4 or 8) for IVF-PQ indexes.",
    )

    # STATIC (constrained decoding) — lives in MAS, accessed via API
    static_enabled: bool = Field(
        False,
        description="Enable STATIC constrained decoding integration (requires MAS).",
    )
    static_mas_endpoint: Optional[str] = Field(
        None,
        description="MAS STATIC endpoint for constrained decoding requests.",
    )
    static_max_constraints: int = Field(
        1_000_000,
        description="Max number of valid token sequences for STATIC index.",
    )
    static_dense_layers: int = Field(
        2,
        description="Number of dense lookup layers in STATIC (d parameter).",
    )

    # MICA integration
    mica_hash_batch_size: int = Field(
        10_000,
        description="Batch size for GPU-accelerated BLAKE3 hashing into MICA.",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
        "env_prefix": "",
    }


gpu_config = GPUConfig()
