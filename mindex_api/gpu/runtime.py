"""
GPU Runtime Manager — MINDEX
==============================
Singleton that manages GPU lifecycle: detection, memory pool initialization,
device health monitoring, and graceful shutdown.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from . import CUDF_AVAILABLE, CUPY_AVAILABLE, CUVS_AVAILABLE, GPU_AVAILABLE
from .config import gpu_config

logger = logging.getLogger(__name__)


@dataclass
class GPUDeviceInfo:
    """Snapshot of GPU device state."""

    available: bool = False
    device_id: int = 0
    device_name: str = "none"
    cuda_version: str = "n/a"
    driver_version: str = "n/a"
    vram_total_gb: float = 0.0
    vram_free_gb: float = 0.0
    vram_used_gb: float = 0.0
    compute_capability: str = "n/a"
    cudf_available: bool = False
    cuvs_available: bool = False
    cupy_available: bool = False
    rmm_pool_initialized: bool = False


class GPURuntime:
    """Manages GPU lifecycle for MINDEX.

    Thread-safe singleton — call get_gpu_runtime() for the shared instance.
    All methods are safe to call even when no GPU is present.
    """

    _instance: Optional[GPURuntime] = None

    def __init__(self) -> None:
        self._initialized = False
        self._device_info = GPUDeviceInfo()
        self._rmm_pool = None

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> GPUDeviceInfo:
        """Detect GPU and optionally initialize RMM memory pool.

        Safe to call multiple times — only initializes once.
        Safe to call without GPU — returns a GPUDeviceInfo with available=False.
        """
        if self._initialized:
            return self._device_info

        self._initialized = True

        if not gpu_config.gpu_enabled:
            logger.info("GPU disabled by configuration (GPU_ENABLED=false)")
            return self._device_info

        if not GPU_AVAILABLE:
            logger.info("GPU packages not installed — running in CPU mode")
            return self._device_info

        try:
            self._detect_device()
            self._init_memory_pool()
        except Exception as e:
            logger.warning("GPU initialization failed: %s — falling back to CPU", e)
            self._device_info.available = False

        return self._device_info

    def _detect_device(self) -> None:
        """Probe CUDA device properties via cupy."""
        import cupy as cp

        device_id = gpu_config.gpu_device_id
        os.environ["CUDA_VISIBLE_DEVICES"] = str(device_id)

        device = cp.cuda.Device(0)  # After CUDA_VISIBLE_DEVICES, device 0 is the target
        props = cp.cuda.runtime.getDeviceProperties(0)

        free, total = cp.cuda.runtime.memGetInfo()

        self._device_info = GPUDeviceInfo(
            available=True,
            device_id=device_id,
            device_name=props["name"].decode() if isinstance(props["name"], bytes) else str(props["name"]),
            cuda_version=".".join(str(x) for x in cp.cuda.runtime.runtimeGetVersion()),
            driver_version=str(cp.cuda.runtime.driverGetVersion()),
            vram_total_gb=round(total / (1024**3), 2),
            vram_free_gb=round(free / (1024**3), 2),
            vram_used_gb=round((total - free) / (1024**3), 2),
            compute_capability=f"{props['major']}.{props['minor']}",
            cudf_available=CUDF_AVAILABLE,
            cuvs_available=CUVS_AVAILABLE,
            cupy_available=CUPY_AVAILABLE,
        )
        logger.info(
            "GPU detected: %s (%s VRAM, CUDA %s, CC %s)",
            self._device_info.device_name,
            f"{self._device_info.vram_total_gb:.1f}GB",
            self._device_info.cuda_version,
            self._device_info.compute_capability,
        )

    def _init_memory_pool(self) -> None:
        """Initialize RMM memory pool for cuDF/cuVS."""
        try:
            import rmm
        except ImportError:
            logger.debug("RMM not installed — using default CUDA allocator")
            return

        pool_type = gpu_config.gpu_memory_pool_type
        limit = gpu_config.gpu_memory_limit_gb

        if limit > 0:
            pool_size = int(limit * 1024**3)
        else:
            # Use 80% of free VRAM
            pool_size = int(self._device_info.vram_free_gb * 0.8 * 1024**3)

        if pool_type == "managed":
            rmm.reinitialize(
                managed_memory=True,
                pool_allocator=True,
                initial_pool_size=pool_size,
            )
        elif pool_type == "pool":
            rmm.reinitialize(
                managed_memory=False,
                pool_allocator=True,
                initial_pool_size=pool_size,
            )
        else:
            # "cuda" — use default allocator, just set cupy to use RMM
            rmm.reinitialize(managed_memory=False, pool_allocator=False)

        self._device_info.rmm_pool_initialized = True
        logger.info(
            "RMM memory pool initialized: type=%s, size=%.1fGB",
            pool_type,
            pool_size / (1024**3),
        )

    # ------------------------------------------------------------------
    # Health / Status
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether GPU is initialized and usable."""
        if not self._initialized:
            self.initialize()
        return self._device_info.available

    @property
    def device_info(self) -> GPUDeviceInfo:
        """Current GPU device information."""
        if not self._initialized:
            self.initialize()
        return self._device_info

    def health_status(self) -> Dict[str, Any]:
        """Health check data for /health/detailed endpoint."""
        info = self.device_info
        if not info.available:
            return {
                "available": False,
                "reason": "disabled" if not gpu_config.gpu_enabled else "no_hardware",
                "cudf_available": CUDF_AVAILABLE,
                "cuvs_available": CUVS_AVAILABLE,
            }

        # Refresh VRAM stats
        try:
            import cupy as cp

            free, total = cp.cuda.runtime.memGetInfo()
            info.vram_free_gb = round(free / (1024**3), 2)
            info.vram_used_gb = round((total - free) / (1024**3), 2)
        except Exception:
            pass

        return {
            "available": True,
            "device": info.device_name,
            "device_id": info.device_id,
            "cuda_version": info.cuda_version,
            "compute_capability": info.compute_capability,
            "vram_total_gb": info.vram_total_gb,
            "vram_free_gb": info.vram_free_gb,
            "vram_used_gb": info.vram_used_gb,
            "rmm_pool": info.rmm_pool_initialized,
            "cudf_available": info.cudf_available,
            "cuvs_available": info.cuvs_available,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Release GPU resources."""
        if not self._device_info.available:
            return
        try:
            import cupy as cp

            cp.get_default_memory_pool().free_all_blocks()
            logger.info("GPU memory pool released")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_runtime: Optional[GPURuntime] = None


def get_gpu_runtime() -> GPURuntime:
    """Get the shared GPURuntime singleton."""
    global _runtime
    if _runtime is None:
        _runtime = GPURuntime()
    return _runtime
