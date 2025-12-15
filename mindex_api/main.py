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
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        docs_url='/docs',
        openapi_url='/openapi.json',
    )

    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.api_cors_origins],
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
        )

    app.include_router(health_router)
    app.include_router(taxon_router)
    app.include_router(telemetry_router)
    app.include_router(devices_router)
    app.include_router(observations_router)
    app.include_router(ip_assets_router)
    app.include_router(mycobrain_router)

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
