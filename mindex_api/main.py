from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .auth import require_internal_token
from .middleware import (
    MeteringMiddleware,
    OutputSanitizerMiddleware,
    RateLimitMiddleware,
    RequestValidationMiddleware,
    SecurityHeadersMiddleware,
)
from .routers import (
    a2a_agent_router,
    beta_router,
    grounding_router,
    compounds_router,
    devices_router,
    earth_router,
    ingest_alias_router,
    fci_router,
    genetics_router,
    health_router,
    images_router,
    ip_assets_router,
    key_management_router,
    knowledge_router,
    mycobrain_router,
    nlm_router,
    observations_router,
    plasticity_router,
    research_router,
    investigation_router,
    search_answers_router,
    stats_router,
    taxon_router,
    telemetry_router,
    unified_search_router,
    rag_retrieve_router,
    wifisense_router,
    drone_router,
    etl_router,
    phylogeny_router,
    genomes_router,
    ledger_router,
    mwave_router,
    emissions_router,
    maritime_router,
    taco_router,
    fusarium_analytics_router,
    fusarium_catalog_router,
    live_state_router,
    mycodao_zone_router,
)
from .routers.worldview import (
    worldview_search_router,
    worldview_earth_router,
    worldview_species_router,
    worldview_answers_router,
    worldview_research_router,
    worldview_manifest_router,
)

try:
    from .routers.worldview import worldview_maritime_router
except Exception:
    worldview_maritime_router = None

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """
    Create and configure the MINDEX FastAPI application.

    Three-zone architecture:
    - Utility (/api/mindex): Health checks, beta onboarding (open or lightly protected)
    - Internal (/api/mindex/internal): Service-to-service (MAS, MycoBrain, NLM, etc.)
    - Worldview (/api/worldview/v1): Read-only curated data for paying users
    """
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=(
            "MINDEX API — Unified Earth Data Platform for the Mycosoft ecosystem. "
            "Three-zone architecture: Internal APIs for service-to-service communication, "
            "Worldview API for paying users (humans & agents), and Utility endpoints "
            "for health checks and onboarding."
        ),
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )

    @app.get("/health", include_in_schema=False)
    async def root_health() -> dict:
        return {"status": "healthy"}

    # =========================================================================
    # MIDDLEWARE STACK (order matters: outermost applied first)
    # =========================================================================

    # CORS — internal zone gets no CORS, worldview gets strict origins
    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.api_cors_origins],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Security headers on Worldview responses
    app.add_middleware(SecurityHeadersMiddleware, path_prefix=settings.worldview_prefix)

    # Request validation on Worldview (read-only enforcement, query length, injection detection)
    app.add_middleware(RequestValidationMiddleware, path_prefix=settings.worldview_prefix)

    # Output sanitization on Worldview (strip internal data, secrets, injection patterns)
    app.add_middleware(OutputSanitizerMiddleware, path_prefix=settings.worldview_prefix)

    # Usage metering (record API calls for billing and audit)
    app.add_middleware(MeteringMiddleware, path_prefix=settings.worldview_prefix)

    # Rate limiting on Worldview (per-key Redis sliding window)
    if settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware, path_prefix=settings.worldview_prefix)

    # =========================================================================
    # ZONE 1: UTILITY (open or lightly protected)
    # =========================================================================
    prefix = settings.api_prefix

    app.include_router(health_router, prefix=prefix)
    app.include_router(beta_router, prefix=prefix)

    # =========================================================================
    # ZONE 2: INTERNAL (service-to-service, requires X-Internal-Token)
    # =========================================================================
    internal_prefix = settings.internal_prefix
    internal_deps = [Depends(require_internal_token)]

    # Device & telemetry routers
    app.include_router(mycobrain_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(telemetry_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(devices_router, prefix=internal_prefix, dependencies=internal_deps)

    # AI/ML routers
    app.include_router(grounding_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(plasticity_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(nlm_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(live_state_router, prefix=internal_prefix, dependencies=internal_deps)

    # Specialized hardware routers
    app.include_router(fci_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(wifisense_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(drone_router, prefix=internal_prefix, dependencies=internal_deps)

    # Data management routers (internal write access)
    app.include_router(investigation_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(images_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(knowledge_router, prefix=internal_prefix, dependencies=internal_deps)
    app.include_router(ip_assets_router, prefix=internal_prefix, dependencies=internal_deps)

    # Agent-to-agent delegation (MAS → MINDEX)
    app.include_router(a2a_agent_router, prefix=internal_prefix, dependencies=internal_deps)

    # Internal write endpoints for search answers (MAS orchestrator)
    app.include_router(search_answers_router, prefix=internal_prefix, dependencies=internal_deps)

    # API key management (CRUD, rotation, usage, audit)
    app.include_router(key_management_router, prefix=internal_prefix, dependencies=internal_deps)

    # MYCODAO schema (mycodao.*) + Mycosoft zone registry (mycosoft.*); MYCA sees both via internal token
    if mycodao_zone_router is not None:
        app.include_router(mycodao_zone_router, prefix=internal_prefix, dependencies=internal_deps)

    # GPU acceleration (cuDF, cuVS, STATIC) — optional, degrades to CPU
    try:
        from .gpu.router import gpu_router

        app.include_router(gpu_router, prefix=internal_prefix, dependencies=internal_deps)
        logger.info("GPU router registered at %s/gpu", internal_prefix)
    except ImportError:
        logger.debug("GPU module not available — skipping GPU router")
    except Exception as exc:
        logger.warning("GPU router failed to load: %s", exc)

    # =========================================================================
    # ZONE 3: WORLDVIEW API (read-only, paying users, DB-backed API key auth)
    # =========================================================================
    worldview_prefix = settings.worldview_prefix

    # Worldview routers (auth is handled per-endpoint via require_worldview_key dependency)
    app.include_router(worldview_search_router, prefix=worldview_prefix)
    app.include_router(worldview_earth_router, prefix=worldview_prefix)
    app.include_router(worldview_species_router, prefix=worldview_prefix)
    app.include_router(worldview_answers_router, prefix=worldview_prefix)
    app.include_router(worldview_research_router, prefix=worldview_prefix)
    app.include_router(worldview_manifest_router, prefix=worldview_prefix)
    if worldview_maritime_router is not None:
        app.include_router(worldview_maritime_router, prefix=worldview_prefix)

    # =========================================================================
    # BACKWARD COMPATIBILITY (deprecated — remove after all consumers migrate)
    # =========================================================================
    # These keep the old /api/mindex/... paths working during the migration window
    # so MAS, CREP, and device consumers don't break.
    app.include_router(taxon_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(telemetry_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(devices_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(mycobrain_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(observations_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(ip_assets_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(stats_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(wifisense_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(drone_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(images_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(knowledge_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(genetics_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(compounds_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(unified_search_router, prefix=prefix, dependencies=internal_deps)
    if rag_retrieve_router is not None:
        app.include_router(rag_retrieve_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(research_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(investigation_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(a2a_agent_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(grounding_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(fci_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(earth_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(ingest_alias_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(plasticity_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(nlm_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(search_answers_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(live_state_router, prefix=prefix, dependencies=internal_deps)
    
    # New integration routers
    app.include_router(etl_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(phylogeny_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(genomes_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(ledger_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(mwave_router, prefix=prefix, dependencies=internal_deps)
    app.include_router(emissions_router, prefix=prefix, dependencies=internal_deps)
    if maritime_router is not None:
        app.include_router(maritime_router, prefix=prefix, dependencies=internal_deps)
    if taco_router is not None:
        app.include_router(taco_router, prefix=prefix, dependencies=internal_deps)
    if fusarium_analytics_router is not None:
        app.include_router(fusarium_analytics_router, prefix=prefix, dependencies=internal_deps)
    if fusarium_catalog_router is not None:
        app.include_router(fusarium_catalog_router, prefix=prefix, dependencies=internal_deps)
    if mycodao_zone_router is not None:
        app.include_router(mycodao_zone_router, prefix=prefix, dependencies=internal_deps)

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


