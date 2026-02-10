"""
MINDEX Knowledge Router - Categories & Knowledge Graph

Provides endpoints for knowledge categories and graph data for MYCA world model.
Created: Feb 10, 2026
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)

knowledge_router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_api_key)],
)


@knowledge_router.get("/categories")
async def list_categories(
    db: AsyncSession = Depends(get_db_session),
    parent_id: Optional[str] = None,
):
    """List all knowledge categories for MYCA world model."""
    # Check if categories table exists, return mock structure if not
    try:
        if parent_id:
            result = await db.execute(
                text(
                    """
                    SELECT id, name, slug, description, parent_id, metadata, created_at
                    FROM app.knowledge_category
                    WHERE parent_id = :parent_id
                    ORDER BY name
                    """
                ),
                {"parent_id": parent_id},
            )
        else:
            result = await db.execute(
                text(
                    """
                    SELECT id, name, slug, description, parent_id, metadata, created_at
                    FROM app.knowledge_category
                    ORDER BY name
                    """
                )
            )
        categories = [dict(row) for row in result.mappings().all()]
        return {"categories": categories, "total": len(categories)}
    except Exception:
        # Table doesn't exist - return core category structure
        core_categories = [
            {
                "id": "fungi",
                "name": "Fungi Knowledge",
                "slug": "fungi",
                "description": "Mycology, taxonomy, species, compounds",
                "parent_id": None,
            },
            {
                "id": "earth",
                "name": "Earth Systems",
                "slug": "earth",
                "description": "Weather, climate, geology, predictions",
                "parent_id": None,
            },
            {
                "id": "devices",
                "name": "Devices & Sensors",
                "slug": "devices",
                "description": "MycoBrain, telemetry, IoT sensors",
                "parent_id": None,
            },
            {
                "id": "life",
                "name": "Life & Ecosystems",
                "slug": "life",
                "description": "NatureOS, biodiversity, ecosystems",
                "parent_id": None,
            },
            {
                "id": "science",
                "name": "Scientific Research",
                "slug": "science",
                "description": "Experiments, hypotheses, FCI",
                "parent_id": None,
            },
            {
                "id": "crep",
                "name": "CREP Data",
                "slug": "crep",
                "description": "Aviation, maritime, satellite, weather feeds",
                "parent_id": None,
            },
        ]
        return {"categories": core_categories, "total": len(core_categories)}


@knowledge_router.get("/categories/{category_id}")
async def get_category(
    category_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific knowledge category."""
    try:
        result = await db.execute(
            text(
                """
                SELECT id, name, slug, description, parent_id, metadata, created_at
                FROM app.knowledge_category
                WHERE id = :id OR slug = :id
                """
            ),
            {"id": category_id},
        )
        row = result.mappings().first()
        if row:
            return {"category": dict(row)}
    except Exception:
        pass

    # Fallback for core categories
    core_map = {
        "fungi": {
            "id": "fungi",
            "name": "Fungi Knowledge",
            "slug": "fungi",
            "description": "Mycology, taxonomy, species, compounds",
        },
        "earth": {
            "id": "earth",
            "name": "Earth Systems",
            "slug": "earth",
            "description": "Weather, climate, geology, predictions",
        },
        "devices": {
            "id": "devices",
            "name": "Devices & Sensors",
            "slug": "devices",
            "description": "MycoBrain, telemetry, IoT sensors",
        },
        "life": {
            "id": "life",
            "name": "Life & Ecosystems",
            "slug": "life",
            "description": "NatureOS, biodiversity, ecosystems",
        },
        "science": {
            "id": "science",
            "name": "Scientific Research",
            "slug": "science",
            "description": "Experiments, hypotheses, FCI",
        },
        "crep": {
            "id": "crep",
            "name": "CREP Data",
            "slug": "crep",
            "description": "Aviation, maritime, satellite, weather feeds",
        },
    }
    if category_id in core_map:
        return {"category": core_map[category_id]}
    return {"category": None, "error": "not_found"}


@knowledge_router.get("/summary")
async def knowledge_summary(db: AsyncSession = Depends(get_db_session)):
    """Get knowledge base summary for MYCA world model."""
    stats = {}

    # Taxon count
    try:
        res = await db.execute(text("SELECT count(*) FROM taxon.taxon"))
        stats["taxon_count"] = res.scalar_one()
    except Exception:
        stats["taxon_count"] = 0

    # Observation count
    try:
        res = await db.execute(text("SELECT count(*) FROM app.observation"))
        stats["observation_count"] = res.scalar_one()
    except Exception:
        stats["observation_count"] = 0

    # IP asset count
    try:
        res = await db.execute(text("SELECT count(*) FROM app.ip_asset"))
        stats["ip_asset_count"] = res.scalar_one()
    except Exception:
        stats["ip_asset_count"] = 0

    # Device count
    try:
        res = await db.execute(text("SELECT count(*) FROM telemetry.device"))
        stats["device_count"] = res.scalar_one()
    except Exception:
        stats["device_count"] = 0

    # Image count
    try:
        res = await db.execute(text("SELECT count(*) FROM app.image"))
        stats["image_count"] = res.scalar_one()
    except Exception:
        stats["image_count"] = 0

    return {
        "summary": stats,
        "status": "online",
        "categories_available": 6,
    }
