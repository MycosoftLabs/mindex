"""
Worldview API Routers — Read-only curated data for paying users.

These are thin wrapper routers around internal data queries that:
- Only expose GET endpoints (no POST/PUT/DELETE)
- Strip internal-only domains (telemetry, devices) from results
- Use standardized WorldviewResponse envelope
- Are gated by DB-backed API key auth + rate limiting
"""

from .search import router as worldview_search_router
from .earth import router as worldview_earth_router
from .species import router as worldview_species_router
from .answers import router as worldview_answers_router
from .research import router as worldview_research_router
from .manifest import router as worldview_manifest_router
from .maritime import router as worldview_maritime_router
from .snapshots import router as worldview_snapshots_router

__all__ = [
    "worldview_search_router",
    "worldview_earth_router",
    "worldview_species_router",
    "worldview_answers_router",
    "worldview_research_router",
    "worldview_manifest_router",
    "worldview_maritime_router",
    "worldview_snapshots_router",
]
