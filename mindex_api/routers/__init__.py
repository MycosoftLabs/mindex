from .health import router as health_router
from .taxon import router as taxon_router
from .telemetry import telemetry_router, devices_router
from .observations import router as observations_router
from .ip_assets import router as ip_assets_router
from .mycobrain import mycobrain_router
from .stats import router as stats_router
from .wifisense import router as wifisense_router
from .drone import router as drone_router
from .images import router as images_router
from .knowledge import knowledge_router

# TODO: Fix these routers - they have import issues
# from .compounds import router as compounds_router
# from .fci import router as fci_router

__all__ = [
    "health_router",
    "taxon_router",
    "telemetry_router",
    "devices_router",
    "observations_router",
    "ip_assets_router",
    "mycobrain_router",
    "stats_router",
    "wifisense_router",
    "drone_router",
    "images_router",
    "knowledge_router",
    # "compounds_router",  # TODO: Fix import issues
    # "fci_router",  # TODO: Fix import issues
]
