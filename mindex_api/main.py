from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import (
    devices_router,
    health_router,
    ip_assets_router,
    mycobrain_router,
    observations_router,
    taxon_router,
    telemetry_router,
)


def create_app() -> FastAPI:
    api_prefix = settings.api_prefix.rstrip("/")
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        docs_url=f"{api_prefix}/docs",
        openapi_url=f"{api_prefix}/openapi.json",
    )

    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.api_cors_origins],
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
        )

    # Public API boundary: everything lives under /api/mindex/...
    app.include_router(health_router, prefix=api_prefix)
    app.include_router(taxon_router, prefix=api_prefix)
    app.include_router(telemetry_router, prefix=api_prefix)
    app.include_router(devices_router, prefix=api_prefix)
    app.include_router(observations_router, prefix=api_prefix)
    app.include_router(ip_assets_router, prefix=api_prefix)
    app.include_router(mycobrain_router, prefix=api_prefix)

    return app


app = create_app()


def run() -> None:
    """Entry point for python -m mindex_api."""
    import uvicorn

    uvicorn.run(
        'mindex_api.main:app',
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
