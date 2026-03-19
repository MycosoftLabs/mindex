"""
Worldview API Manifest — machine-readable endpoint registry.

Returns all available Worldview endpoints, their parameters,
authentication requirements, and rate limits per plan tier.
No auth required on this endpoint (it's documentation).
"""

from __future__ import annotations

from fastapi import APIRouter

from ...config import settings

router = APIRouter(tags=["Worldview Manifest"])


@router.get("/manifest")
async def worldview_manifest() -> dict:
    """
    Machine-readable API manifest for the Worldview API.

    Returns all available endpoints, their parameters, domains,
    auth requirements, and rate limits per plan tier.
    """
    return {
        "name": "MINDEX Worldview API",
        "version": "v1",
        "description": (
            "Read-only curated Earth data API. Provides unified search across "
            "all planetary data: species, earth events, atmosphere, water, "
            "infrastructure, signals, transport, space, and more."
        ),
        "base_url": settings.worldview_prefix,
        "auth": {
            "type": "api_key",
            "header": "X-API-Key",
            "description": "Obtain an API key via the /api/mindex/beta/onboard endpoint.",
        },
        "pricing": {
            "activation_fee": "$1.00 USD (one-time)",
            "tiers": {
                "free": {
                    "price": "$0/month",
                    "rate_limit_per_minute": settings.worldview_rate_limits.get("free", {}).get("per_minute", 10),
                    "rate_limit_per_day": settings.worldview_rate_limits.get("free", {}).get("per_day", 1000),
                },
                "pro": {
                    "price": "$29/month",
                    "rate_limit_per_minute": settings.worldview_rate_limits.get("pro", {}).get("per_minute", 60),
                    "rate_limit_per_day": settings.worldview_rate_limits.get("pro", {}).get("per_day", 10000),
                },
                "enterprise": {
                    "price": "Contact sales",
                    "rate_limit_per_minute": settings.worldview_rate_limits.get("enterprise", {}).get("per_minute", 300),
                    "rate_limit_per_day": settings.worldview_rate_limits.get("enterprise", {}).get("per_day", 100000),
                },
            },
        },
        "endpoints": [
            {
                "path": "/search",
                "method": "GET",
                "description": "Unified search across 34+ planetary data domains",
                "parameters": ["q (required)", "domains", "lat", "lng", "radius_km", "limit"],
            },
            {
                "path": "/search/domains",
                "method": "GET",
                "description": "List all searchable domains and domain groups",
            },
            {
                "path": "/earth/stats",
                "method": "GET",
                "description": "Entity counts across all Earth data domains",
            },
            {
                "path": "/earth/map/bbox",
                "method": "GET",
                "description": "Spatial bounding box query for map rendering",
                "parameters": ["layer (required)", "lat_min", "lat_max", "lng_min", "lng_max", "limit"],
            },
            {
                "path": "/earth/map/layers",
                "method": "GET",
                "description": "List available map data layers",
            },
            {
                "path": "/earth/earthquakes/recent",
                "method": "GET",
                "description": "Recent earthquakes with magnitude filtering",
                "parameters": ["hours", "min_magnitude", "limit"],
            },
            {
                "path": "/earth/satellites/active",
                "method": "GET",
                "description": "Active satellites in orbit",
            },
            {
                "path": "/earth/solar/recent",
                "method": "GET",
                "description": "Recent solar events (flares, CMEs)",
            },
            {
                "path": "/earth/infrastructure",
                "method": "GET",
                "description": "Infrastructure within bounding box",
            },
            {
                "path": "/species/taxa",
                "method": "GET",
                "description": "Search and list taxonomic records",
                "parameters": ["q", "rank", "source", "limit", "offset"],
            },
            {
                "path": "/species/taxa/{taxon_id}",
                "method": "GET",
                "description": "Get a specific taxon by ID",
            },
            {
                "path": "/species/observations",
                "method": "GET",
                "description": "Field observations with spatial/temporal filters",
                "parameters": ["taxon_id", "start", "end", "bbox", "limit", "offset"],
            },
            {
                "path": "/species/genetics",
                "method": "GET",
                "description": "Genetic sequences (GenBank, NCBI)",
                "parameters": ["q", "species", "limit", "offset"],
            },
            {
                "path": "/species/compounds",
                "method": "GET",
                "description": "Chemical compounds (ChemSpider, PubChem)",
                "parameters": ["q", "limit", "offset"],
            },
            {
                "path": "/answers/search",
                "method": "GET",
                "description": "Search cached answer snippets and QA pairs",
                "parameters": ["q (required)", "limit"],
            },
            {
                "path": "/answers/qa",
                "method": "GET",
                "description": "List QA pairs",
                "parameters": ["q", "limit"],
            },
            {
                "path": "/answers/worldview-facts",
                "method": "GET",
                "description": "Curated worldview facts with category filtering",
                "parameters": ["category", "limit"],
            },
            {
                "path": "/research/papers",
                "method": "GET",
                "description": "Search research papers (OpenAlex)",
                "parameters": ["q (required)", "limit"],
            },
            {
                "path": "/research/papers/{paper_id}",
                "method": "GET",
                "description": "Get a specific research paper",
            },
            {
                "path": "/research/stats",
                "method": "GET",
                "description": "MINDEX database statistics",
            },
        ],
        "domains": [
            "taxa", "species", "compounds", "genetics", "observations",
            "earthquakes", "volcanoes", "wildfires", "storms", "lightning", "tornadoes", "floods",
            "air_quality", "greenhouse_gas", "weather", "remote_sensing",
            "buoys", "stream_gauges",
            "facilities", "power_grid", "water_systems", "internet_cables",
            "antennas", "wifi_hotspots", "signal_measurements",
            "aircraft", "vessels", "airports", "ports", "spaceports", "launches",
            "satellites", "solar_events",
            "cameras", "military_installations",
            "research", "crep_entities",
        ],
        "response_format": {
            "envelope": True,
            "structure": {
                "data": "The response payload (object or array)",
                "meta": {
                    "request_id": "Unique request identifier",
                    "api_version": "API version (v1)",
                    "count": "Number of items in data",
                    "cached": "Whether the response was served from cache",
                    "timestamp": "ISO 8601 response timestamp",
                    "plan": "Your current plan tier",
                },
            },
        },
        "headers": {
            "request": {
                "X-API-Key": "Your API key (required)",
            },
            "response": {
                "X-RateLimit-Limit-Minute": "Your per-minute rate limit",
                "X-RateLimit-Remaining-Minute": "Remaining requests this minute",
                "X-RateLimit-Limit-Day": "Your daily rate limit",
                "X-RateLimit-Remaining-Day": "Remaining requests today",
            },
        },
    }
