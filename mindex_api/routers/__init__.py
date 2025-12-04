from .health import router as health_router
from .taxon import router as taxon_router
from .telemetry import telemetry_router, devices_router
from .observations import router as observations_router
from .ip_assets import router as ip_assets_router

__all__ = [
    "health_router",
    "taxon_router",
    "telemetry_router",
    "devices_router",
    "observations_router",
    "ip_assets_router",
]
