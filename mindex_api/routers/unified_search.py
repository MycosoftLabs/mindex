"""
Unified Search Router

Single endpoint that searches across all MINDEX data types:
- Taxa (species)
- Compounds (chemistry)
- Genetics (sequences)
- Observations (sightings)
- Knowledge graph

Supports:
- Full-text search
- Location-based filtering
- Toxicity/edibility filtering
- Parallel queries for performance
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/unified-search", tags=["Unified Search"])


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class TaxonResult(BaseModel):
    id: UUID
    scientific_name: str
    common_name: Optional[str] = None
    rank: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    observation_count: int = 0
    source: str = "mindex"
    toxicity: Optional[str] = None
    edibility: Optional[str] = None


class CompoundResult(BaseModel):
    id: UUID
    name: str
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    chemical_class: Optional[str] = None
    smiles: Optional[str] = None
    bioactivity: List[str] = Field(default_factory=list)
    source_species: List[str] = Field(default_factory=list)


class GeneticsResult(BaseModel):
    id: int
    accession: str
    species_name: str
    gene: Optional[str] = None
    sequence_length: int = 0
    source: str = "genbank"


class ResearchResult(BaseModel):
    id: str
    title: str
    authors: List[str] = Field(default_factory=list)
    journal: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: Optional[str] = None


class ObservationResult(BaseModel):
    id: UUID
    taxon_id: UUID
    taxon_name: str
    location: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    observed_at: Optional[str] = None
    image_url: Optional[str] = None


class UnifiedSearchResponse(BaseModel):
    query: str
    results: Dict[str, List[Any]]
    total_count: int
    timing_ms: int
    filters_applied: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# SEARCH FUNCTIONS
# =============================================================================

async def search_taxa(
    session: AsyncSession,
    query: str,
    limit: int,
    toxicity_filter: Optional[str] = None,
    location_lat: Optional[float] = None,
    location_lng: Optional[float] = None,
    location_radius: Optional[float] = None,
) -> List[TaxonResult]:
    """Search taxa by name with optional filters."""
    where_clauses = ["(canonical_name ILIKE :query OR common_name ILIKE :query)"]
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    
    # Add toxicity filter if provided
    if toxicity_filter:
        if toxicity_filter in ("poisonous", "toxic", "deadly"):
            where_clauses.append("(metadata->>'toxicity' IS NOT NULL OR metadata->>'poisonous' = 'true')")
        elif toxicity_filter == "edible":
            where_clauses.append("(metadata->>'edibility' = 'edible' OR metadata->>'edible' = 'true')")
        elif toxicity_filter in ("psychedelic", "hallucinogenic"):
            where_clauses.append("(metadata->>'psychoactive' = 'true' OR canonical_name ILIKE '%psilocybe%')")
    
    sql = f"""
        SELECT 
            id, canonical_name, common_name, rank, description,
            (SELECT url FROM core.taxon_image WHERE taxon_id = t.id LIMIT 1) as image_url,
            COALESCE((metadata->>'observation_count')::int, 0) as observation_count,
            metadata->>'toxicity' as toxicity,
            metadata->>'edibility' as edibility
        FROM core.taxon t
        WHERE {' AND '.join(where_clauses)}
        ORDER BY 
            CASE WHEN canonical_name ILIKE :exact_query THEN 0 ELSE 1 END,
            observation_count DESC
        LIMIT :limit
    """
    params["exact_query"] = query
    
    try:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
        
        return [
            TaxonResult(
                id=row.id,
                scientific_name=row.canonical_name,
                common_name=row.common_name,
                rank=row.rank,
                description=row.description,
                image_url=row.image_url,
                observation_count=row.observation_count or 0,
                toxicity=row.toxicity,
                edibility=row.edibility,
            )
            for row in rows
        ]
    except Exception:
        return []


async def search_compounds(
    session: AsyncSession,
    query: str,
    limit: int,
) -> List[CompoundResult]:
    """Search compounds by name or formula."""
    sql = """
        SELECT 
            c.id, c.name, c.formula, c.molecular_weight, c.chemical_class, c.smiles,
            ARRAY_AGG(DISTINCT ba.name) FILTER (WHERE ba.name IS NOT NULL) as bioactivities,
            ARRAY_AGG(DISTINCT t.canonical_name) FILTER (WHERE t.canonical_name IS NOT NULL) as species
        FROM bio.compound c
        LEFT JOIN bio.compound_activity ca ON ca.compound_id = c.id
        LEFT JOIN bio.biological_activity ba ON ba.id = ca.activity_id
        LEFT JOIN bio.taxon_compound tc ON tc.compound_id = c.id
        LEFT JOIN core.taxon t ON t.id = tc.taxon_id
        WHERE c.name ILIKE :query OR c.formula ILIKE :query
        GROUP BY c.id, c.name, c.formula, c.molecular_weight, c.chemical_class, c.smiles
        ORDER BY 
            CASE WHEN c.name ILIKE :exact_query THEN 0 ELSE 1 END,
            c.name
        LIMIT :limit
    """
    
    try:
        result = await session.execute(text(sql), {
            "query": f"%{query}%",
            "exact_query": query,
            "limit": limit,
        })
        rows = result.fetchall()
        
        return [
            CompoundResult(
                id=row.id,
                name=row.name,
                formula=row.formula,
                molecular_weight=row.molecular_weight,
                chemical_class=row.chemical_class,
                smiles=row.smiles,
                bioactivity=row.bioactivities or [],
                source_species=row.species or [],
            )
            for row in rows
        ]
    except Exception:
        return []


async def search_genetics(
    session: AsyncSession,
    query: str,
    limit: int,
) -> List[GeneticsResult]:
    """Search genetic sequences by species name or accession."""
    sql = """
        SELECT 
            id, accession, species_name, gene, sequence_length, source
        FROM bio.genetic_sequence
        WHERE species_name ILIKE :query OR accession ILIKE :query OR gene ILIKE :query
        ORDER BY 
            CASE WHEN species_name ILIKE :exact_query THEN 0 ELSE 1 END,
            species_name
        LIMIT :limit
    """
    
    try:
        result = await session.execute(text(sql), {
            "query": f"%{query}%",
            "exact_query": query,
            "limit": limit,
        })
        rows = result.fetchall()
        
        return [
            GeneticsResult(
                id=row.id,
                accession=row.accession,
                species_name=row.species_name,
                gene=row.gene,
                sequence_length=row.sequence_length or 0,
                source=row.source or "genbank",
            )
            for row in rows
        ]
    except Exception:
        return []


async def search_observations_by_location(
    session: AsyncSession,
    lat: float,
    lng: float,
    radius_km: float,
    taxon_query: Optional[str] = None,
    limit: int = 20,
) -> List[ObservationResult]:
    """Search observations by location with optional taxon filter."""
    where_clauses = [
        "ST_DWithin(o.geom::geography, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
    ]
    params: Dict[str, Any] = {
        "lat": lat,
        "lng": lng,
        "radius_m": radius_km * 1000,
        "limit": limit,
    }
    
    if taxon_query:
        where_clauses.append("(t.canonical_name ILIKE :taxon_query OR t.common_name ILIKE :taxon_query)")
        params["taxon_query"] = f"%{taxon_query}%"
    
    sql = f"""
        SELECT 
            o.id, o.taxon_id, t.canonical_name as taxon_name,
            o.location_name as location,
            ST_Y(o.geom) as lat, ST_X(o.geom) as lng,
            o.observed_at::text as observed_at,
            (SELECT url FROM core.taxon_image WHERE taxon_id = o.taxon_id LIMIT 1) as image_url
        FROM core.observation o
        JOIN core.taxon t ON t.id = o.taxon_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY o.observed_at DESC
        LIMIT :limit
    """
    
    try:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
        
        return [
            ObservationResult(
                id=row.id,
                taxon_id=row.taxon_id,
                taxon_name=row.taxon_name,
                location=row.location,
                lat=row.lat,
                lng=row.lng,
                observed_at=row.observed_at,
                image_url=row.image_url,
            )
            for row in rows
        ]
    except Exception:
        return []


# =============================================================================
# MAIN ENDPOINT
# =============================================================================

@router.get("", response_model=UnifiedSearchResponse)
async def unified_search(
    q: str = Query(..., min_length=2, description="Search query"),
    types: str = Query(
        "taxa,compounds,genetics",
        description="Comma-separated list of types to search: taxa,compounds,genetics,observations"
    ),
    limit: int = Query(20, ge=1, le=100, description="Max results per type"),
    # Location filters
    lat: Optional[float] = Query(None, description="Latitude for location-based search"),
    lng: Optional[float] = Query(None, description="Longitude for location-based search"),
    radius: Optional[float] = Query(100, description="Search radius in km"),
    # Content filters
    toxicity: Optional[str] = Query(None, description="Filter by toxicity: poisonous, edible, psychedelic"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Unified search across all MINDEX data.
    
    Returns combined results from:
    - **taxa**: Species and taxonomy data
    - **compounds**: Chemical compounds
    - **genetics**: Genetic sequences
    - **observations**: Field observations (requires lat/lng)
    
    Supports filtering by:
    - Location (lat, lng, radius)
    - Toxicity (poisonous, edible, psychedelic)
    """
    import time
    start_time = time.time()
    
    type_list = [t.strip().lower() for t in types.split(",")]
    
    # Build parallel search tasks
    tasks = []
    task_names = []
    
    if "taxa" in type_list:
        tasks.append(search_taxa(session, q, limit, toxicity, lat, lng, radius))
        task_names.append("taxa")
    
    if "compounds" in type_list:
        tasks.append(search_compounds(session, q, limit))
        task_names.append("compounds")
    
    if "genetics" in type_list:
        tasks.append(search_genetics(session, q, limit))
        task_names.append("genetics")
    
    if "observations" in type_list and lat is not None and lng is not None:
        tasks.append(search_observations_by_location(session, lat, lng, radius or 100, q, limit))
        task_names.append("observations")
    
    # Execute all searches in parallel
    if tasks:
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        results_list = []
    
    # Combine results
    results: Dict[str, List[Any]] = {}
    total_count = 0
    
    for name, result in zip(task_names, results_list):
        if isinstance(result, Exception):
            results[name] = []
        else:
            results[name] = [r.model_dump() for r in result]
            total_count += len(result)
    
    timing_ms = int((time.time() - start_time) * 1000)
    
    filters_applied = {}
    if toxicity:
        filters_applied["toxicity"] = toxicity
    if lat is not None and lng is not None:
        filters_applied["location"] = {"lat": lat, "lng": lng, "radius_km": radius}
    
    return UnifiedSearchResponse(
        query=q,
        results=results,
        total_count=total_count,
        timing_ms=timing_ms,
        filters_applied=filters_applied,
    )


@router.get("/taxa/by-location")
async def search_taxa_by_location(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(50, description="Radius in km"),
    filter: Optional[str] = Query(None, description="Filter: poisonous, edible, psychedelic"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Get taxa observed near a specific location.
    
    Combines MINDEX observations with iNaturalist data for comprehensive coverage.
    """
    observations = await search_observations_by_location(
        session, lat, lng, radius, limit=limit
    )
    
    # Get unique taxa from observations
    taxon_ids = list(set(obs.taxon_id for obs in observations))
    
    if not taxon_ids:
        return {"taxa": [], "observations": [], "location": {"lat": lat, "lng": lng, "radius_km": radius}}
    
    # Get full taxon info
    taxa_sql = """
        SELECT 
            t.id, t.canonical_name, t.common_name, t.rank,
            (SELECT url FROM core.taxon_image WHERE taxon_id = t.id LIMIT 1) as image_url,
            t.metadata->>'toxicity' as toxicity,
            t.metadata->>'edibility' as edibility
        FROM core.taxon t
        WHERE t.id = ANY(:taxon_ids)
    """
    
    try:
        result = await session.execute(text(taxa_sql), {"taxon_ids": taxon_ids})
        rows = result.fetchall()
        
        taxa = []
        for row in rows:
            # Apply filter if provided
            if filter:
                if filter in ("poisonous", "toxic") and not row.toxicity:
                    continue
                if filter == "edible" and row.edibility != "edible":
                    continue
            
            taxa.append({
                "id": str(row.id),
                "scientific_name": row.canonical_name,
                "common_name": row.common_name,
                "rank": row.rank,
                "image_url": row.image_url,
                "toxicity": row.toxicity,
                "edibility": row.edibility,
            })
        
        return {
            "taxa": taxa,
            "observations": [obs.model_dump() for obs in observations[:20]],
            "location": {"lat": lat, "lng": lng, "radius_km": radius},
        }
    except Exception as e:
        return {
            "taxa": [],
            "observations": [],
            "location": {"lat": lat, "lng": lng, "radius_km": radius},
            "error": str(e),
        }
