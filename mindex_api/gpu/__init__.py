"""
GPU Acceleration Module — MINDEX
=================================
Optional GPU acceleration via NVIDIA RAPIDS (cuDF + cuVS) and STATIC
constrained decoding. All GPU functionality degrades gracefully to CPU
when CUDA/RAPIDS packages are not installed.

Components:
    - runtime: GPU detection, CUDA health, VRAM management
    - cudf_engine: DataFrame operations (cuDF → pandas fallback)
    - cuvs_index: Vector similarity search (cuVS → pgvector fallback)
    - static_bridge: Constrained decoding via MAS STATIC integration
    - mica_bridge: Merkle-verified GPU computation results
    - router: Internal API endpoints for GPU services
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Probe GPU availability at import time (no side effects, just detection)
# ---------------------------------------------------------------------------

GPU_AVAILABLE = False
CUDF_AVAILABLE = False
CUVS_AVAILABLE = False
CUPY_AVAILABLE = False

try:
    import cupy  # noqa: F401

    CUPY_AVAILABLE = True
except ImportError:
    pass

try:
    import cudf  # noqa: F401

    CUDF_AVAILABLE = True
except ImportError:
    pass

try:
    import cuvs  # noqa: F401

    CUVS_AVAILABLE = True
except ImportError:
    pass

# GPU is considered available if at least cupy works (CUDA runtime present)
GPU_AVAILABLE = CUPY_AVAILABLE


def get_availability() -> dict:
    """Return a dict summarizing GPU library availability."""
    return {
        "gpu_available": GPU_AVAILABLE,
        "cudf_available": CUDF_AVAILABLE,
        "cuvs_available": CUVS_AVAILABLE,
        "cupy_available": CUPY_AVAILABLE,
    }


if GPU_AVAILABLE:
    logger.info("GPU acceleration available: cuDF=%s cuVS=%s", CUDF_AVAILABLE, CUVS_AVAILABLE)
else:
    logger.info("GPU acceleration not available — using CPU fallback for all operations")
