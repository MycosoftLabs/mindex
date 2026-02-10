from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import (
    compounds_router,
    devices_router,
    health_router,
    images_router,
    ip_assets_router,
    knowledge_router,
    mycobrain_router,
    observations_router,
    stats_router,
    taxon_router,
    telemetry_router,
    wifisense_router,
    drone_router,
)


def create_app() -> FastAPI:
    """
    Create and configure the MINDEX FastAPI application.
    
    Includes routers for:
    - Health checks
    - Taxon management (mycological taxonomy)
    - Telemetry (generic device data)
    - MycoBrain (MDP v1 device integration)
    - Observations (field observations)
    - IP Assets (blockchain anchoring)
    - WiFi Sense (CSI-based sensing)
    - MycoDRONE (autonomous deployment/recovery)
    """
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=(
            "MINDEX API - Core data platform for the Mycosoft ecosystem. "
            "Integrates with MycoBrain devices via MDP v1, bridges to NatureOS "
            "through the Mycorrhizae Protocol, and provides AI/ML-ready data pipelines."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.api_cors_origins],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Core routers - all under the api_prefix
    prefix = settings.api_prefix
    
    app.include_router(health_router, prefix=prefix)
    app.include_router(taxon_router, prefix=prefix)
    
    # Telemetry routers (legacy generic + MycoBrain-specific)
    app.include_router(telemetry_router, prefix=prefix)
    app.include_router(devices_router, prefix=prefix)
    app.include_router(mycobrain_router, prefix=prefix)
    
    # Data routers
    app.include_router(observations_router, prefix=prefix)
    app.include_router(ip_assets_router, prefix=prefix)
    app.include_router(stats_router, prefix=prefix)
    
    # Feature routers (WiFi Sense and MycoDRONE)
    app.include_router(wifisense_router, prefix=prefix)
    app.include_router(drone_router, prefix=prefix)
    
    # Image management router
    app.include_router(images_router, prefix=prefix)
    
    # Chemistry / Compounds router (ChemSpider integration)
    app.include_router(compounds_router, prefix=prefix)

    # Knowledge router (categories, knowledge graph for MYCA world model)
    app.include_router(knowledge_router, prefix=prefix)

    return app


app = create_app()


def run() -> None:
    """Entry point for `python -m mindex_api`."""
    import uvicorn

    uvicorn.run(
        "mindex_api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
