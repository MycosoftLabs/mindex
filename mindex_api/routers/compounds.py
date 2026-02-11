"""
Compounds Router

API endpoints for chemical compound data with ChemSpider integration.
"""

from __future__ import annotations

import os
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..dependencies import get_db_session, require_api_key
from ..schemas.compound import (
    BiologicalActivityResponse,
    ChemSpiderEnrichRequest,
    ChemSpiderEnrichResponse,
    ChemSpiderSearchRequest,
    ChemSpiderSearchResponse,
    ChemSpiderSearchResult,
    CompoundCreate,
    CompoundForTaxonResponse,
    CompoundListResponse,
    CompoundPropertyCreate,
    CompoundPropertyResponse,
    CompoundResponse,
    CompoundSearchRequest,
    CompoundUpdate,
    TaxonCompoundCreate,
    TaxonCompoundResponse,
    TaxonCompoundsResponse,
)

router = APIRouter(prefix="/compounds", tags=["Compounds"])


# =============================================================================
# COMPOUND CRUD ENDPOINTS
# =============================================================================

@router.get("", response_model=CompoundListResponse)
async def list_compounds(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    chemical_class: Optional[str] = None,
    compound_type: Optional[str] = None,
    search: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
):
    """
    List all compounds with optional filtering.
    
    - **skip**: Number of records to skip (pagination)
    - **limit**: Maximum records to return
    - **chemical_class**: Filter by chemical class
    - **compound_type**: Filter by compound type
    - **search**: Search in compound names
    """
    # Build query with filters
    where_clauses = []
    params = {"skip": skip, "limit": limit}
    
    if chemical_class:
        where_clauses.append("chemical_class = :chemical_class")
        params["chemical_class"] = chemical_class
    
    if compound_type:
        where_clauses.append("compound_type = :compound_type")
        params["compound_type"] = compound_type
    
    if search:
        where_clauses.append("name ILIKE :search")
        params["search"] = f"%{search}%"
    
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    
    # Get total count
    count_sql = f"SELECT COUNT(*) FROM bio.compound {where_sql}"
    count_result = await session.execute(text(count_sql), params)
    total_count = count_result.scalar() or 0
    
    # Get compounds
    query_sql = f"""
        SELECT 
            id, name, iupac_name, formula, molecular_weight, monoisotopic_mass,
            smiles, inchi, inchikey, chemspider_id, pubchem_id, cas_number,
            chebi_id, chemical_class, compound_type, source, metadata,
            created_at, updated_at
        FROM bio.compound
        {where_sql}
        ORDER BY name
        OFFSET :skip LIMIT :limit
    """
    
    result = await session.execute(text(query_sql), params)
    rows = result.fetchall()
    
    compounds = []
    for row in rows:
        compounds.append(CompoundResponse(
            id=row.id,
            name=row.name,
            iupac_name=row.iupac_name,
            formula=row.formula,
            molecular_weight=row.molecular_weight,
            monoisotopic_mass=row.monoisotopic_mass,
            smiles=row.smiles,
            inchi=row.inchi,
            inchikey=row.inchikey,
            chemspider_id=row.chemspider_id,
            pubchem_id=row.pubchem_id,
            cas_number=row.cas_number,
            chebi_id=row.chebi_id,
            chemical_class=row.chemical_class,
            compound_type=row.compound_type,
            source=row.source,
            metadata=row.metadata or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        ))
    
    return CompoundListResponse(
        data=compounds,
        limit=limit,
        offset=skip,
        total=total_count,
    )


@router.get("/{compound_id}", response_model=CompoundResponse)
async def get_compound(
    compound_id: UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Get a single compound by ID with full details."""
    query = text("""
        SELECT 
            c.id, c.name, c.iupac_name, c.formula, c.molecular_weight, c.monoisotopic_mass,
            c.smiles, c.inchi, c.inchikey, c.chemspider_id, c.pubchem_id, c.cas_number,
            c.chebi_id, c.chemical_class, c.compound_type, c.source, c.metadata,
            c.created_at, c.updated_at,
            (SELECT COUNT(*) FROM bio.taxon_compound tc WHERE tc.compound_id = c.id) as species_count
        FROM bio.compound c
        WHERE c.id = :compound_id
    """)
    
    result = await session.execute(query, {"compound_id": compound_id})
    row = result.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compound {compound_id} not found",
        )
    
    # Get activities
    activities_query = text("""
        SELECT ba.id, ba.name, ba.category, ca.potency, ca.evidence_level
        FROM bio.compound_activity ca
        JOIN bio.biological_activity ba ON ba.id = ca.activity_id
        WHERE ca.compound_id = :compound_id
        ORDER BY ba.category, ba.name
    """)
    activities_result = await session.execute(activities_query, {"compound_id": compound_id})
    activities = [
        {
            "activity_id": a.id,
            "activity_name": a.name,
            "category": a.category,
            "potency": a.potency,
            "evidence_level": a.evidence_level,
        }
        for a in activities_result.fetchall()
    ]
    
    return CompoundResponse(
        id=row.id,
        name=row.name,
        iupac_name=row.iupac_name,
        formula=row.formula,
        molecular_weight=row.molecular_weight,
        monoisotopic_mass=row.monoisotopic_mass,
        smiles=row.smiles,
        inchi=row.inchi,
        inchikey=row.inchikey,
        chemspider_id=row.chemspider_id,
        pubchem_id=row.pubchem_id,
        cas_number=row.cas_number,
        chebi_id=row.chebi_id,
        chemical_class=row.chemical_class,
        compound_type=row.compound_type,
        source=row.source,
        metadata=row.metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        activities=activities,
        species_count=row.species_count,
    )


@router.post("", response_model=CompoundResponse, status_code=status.HTTP_201_CREATED)
async def create_compound(
    compound: CompoundCreate,
    session: AsyncSession = Depends(get_db_session),
):
    """Create a new compound."""
    query = text("""
        INSERT INTO bio.compound (
            name, iupac_name, formula, molecular_weight, smiles, inchi, inchikey,
            chemspider_id, pubchem_id, cas_number, chebi_id, chemical_class,
            compound_type, source, metadata
        ) VALUES (
            :name, :iupac_name, :formula, :molecular_weight, :smiles, :inchi, :inchikey,
            :chemspider_id, :pubchem_id, :cas_number, :chebi_id, :chemical_class,
            :compound_type, :source, :metadata
        )
        RETURNING id, created_at, updated_at
    """)
    
    result = await session.execute(query, {
        "name": compound.name,
        "iupac_name": compound.iupac_name,
        "formula": compound.formula,
        "molecular_weight": compound.molecular_weight,
        "smiles": compound.smiles,
        "inchi": compound.inchi,
        "inchikey": compound.inchikey,
        "chemspider_id": compound.chemspider_id,
        "pubchem_id": compound.pubchem_id,
        "cas_number": compound.cas_number,
        "chebi_id": compound.chebi_id,
        "chemical_class": compound.chemical_class,
        "compound_type": compound.compound_type,
        "source": compound.source,
        "metadata": compound.metadata,
    })
    
    await session.commit()
    row = result.fetchone()
    
    return CompoundResponse(
        id=row.id,
        name=compound.name,
        iupac_name=compound.iupac_name,
        formula=compound.formula,
        molecular_weight=compound.molecular_weight,
        smiles=compound.smiles,
        inchi=compound.inchi,
        inchikey=compound.inchikey,
        chemspider_id=compound.chemspider_id,
        pubchem_id=compound.pubchem_id,
        cas_number=compound.cas_number,
        chebi_id=compound.chebi_id,
        chemical_class=compound.chemical_class,
        compound_type=compound.compound_type,
        source=compound.source,
        metadata=compound.metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/search", response_model=CompoundListResponse)
async def search_compounds(
    request: CompoundSearchRequest,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),
):
    """Search compounds with advanced filters."""
    where_clauses = []
    params = {"skip": skip, "limit": limit}
    
    if request.query:
        where_clauses.append("name ILIKE :query")
        params["query"] = f"%{request.query}%"
    
    if request.formula:
        where_clauses.append("formula = :formula")
        params["formula"] = request.formula
    
    if request.smiles:
        where_clauses.append("smiles = :smiles")
        params["smiles"] = request.smiles
    
    if request.inchikey:
        where_clauses.append("inchikey = :inchikey")
        params["inchikey"] = request.inchikey
    
    if request.chemical_class:
        where_clauses.append("chemical_class = :chemical_class")
        params["chemical_class"] = request.chemical_class
    
    if request.compound_type:
        where_clauses.append("compound_type = :compound_type")
        params["compound_type"] = request.compound_type
    
    if request.min_molecular_weight:
        where_clauses.append("molecular_weight >= :min_mw")
        params["min_mw"] = request.min_molecular_weight
    
    if request.max_molecular_weight:
        where_clauses.append("molecular_weight <= :max_mw")
        params["max_mw"] = request.max_molecular_weight
    
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    
    # Get total count
    count_sql = f"SELECT COUNT(*) FROM bio.compound {where_sql}"
    count_result = await session.execute(text(count_sql), params)
    total_count = count_result.scalar() or 0
    
    # Get compounds
    query_sql = f"""
        SELECT 
            id, name, iupac_name, formula, molecular_weight, monoisotopic_mass,
            smiles, inchi, inchikey, chemspider_id, pubchem_id, cas_number,
            chebi_id, chemical_class, compound_type, source, metadata,
            created_at, updated_at
        FROM bio.compound
        {where_sql}
        ORDER BY name
        OFFSET :skip LIMIT :limit
    """
    
    result = await session.execute(text(query_sql), params)
    rows = result.fetchall()
    
    compounds = [
        CompoundResponse(
            id=row.id,
            name=row.name,
            iupac_name=row.iupac_name,
            formula=row.formula,
            molecular_weight=row.molecular_weight,
            monoisotopic_mass=row.monoisotopic_mass,
            smiles=row.smiles,
            inchi=row.inchi,
            inchikey=row.inchikey,
            chemspider_id=row.chemspider_id,
            pubchem_id=row.pubchem_id,
            cas_number=row.cas_number,
            chebi_id=row.chebi_id,
            chemical_class=row.chemical_class,
            compound_type=row.compound_type,
            source=row.source,
            metadata=row.metadata or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    
    return CompoundListResponse(
        data=compounds,
        limit=limit,
        offset=skip,
        total=total_count,
    )


# =============================================================================
# TAXON-COMPOUND ENDPOINTS
# =============================================================================

@router.get("/for-taxon/{taxon_id}", response_model=TaxonCompoundsResponse)
async def get_compounds_for_taxon(
    taxon_id: UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Get all compounds associated with a specific taxon."""
    # Get taxon info
    taxon_query = text("""
        SELECT id, canonical_name, common_name
        FROM core.taxon
        WHERE id = :taxon_id
    """)
    taxon_result = await session.execute(taxon_query, {"taxon_id": taxon_id})
    taxon = taxon_result.fetchone()
    
    if not taxon:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Taxon {taxon_id} not found",
        )
    
    # Get compounds
    compounds_query = text("""
        SELECT 
            c.id as compound_id,
            c.name,
            c.formula,
            c.molecular_weight,
            c.chemspider_id,
            tc.relationship_type,
            tc.evidence_level,
            tc.tissue_location
        FROM bio.taxon_compound tc
        JOIN bio.compound c ON c.id = tc.compound_id
        WHERE tc.taxon_id = :taxon_id
        ORDER BY c.name
    """)
    compounds_result = await session.execute(compounds_query, {"taxon_id": taxon_id})
    
    compounds = [
        CompoundForTaxonResponse(
            compound_id=row.compound_id,
            name=row.name,
            formula=row.formula,
            molecular_weight=row.molecular_weight,
            chemspider_id=row.chemspider_id,
            relationship_type=row.relationship_type,
            evidence_level=row.evidence_level,
            tissue_location=row.tissue_location,
        )
        for row in compounds_result.fetchall()
    ]
    
    return TaxonCompoundsResponse(
        taxon_id=taxon.id,
        canonical_name=taxon.canonical_name,
        common_name=taxon.common_name,
        compounds=compounds,
    )


@router.post("/taxon-link", response_model=TaxonCompoundResponse, status_code=status.HTTP_201_CREATED)
async def link_compound_to_taxon(
    link: TaxonCompoundCreate,
    session: AsyncSession = Depends(get_db_session),
):
    """Create a link between a taxon and a compound."""
    query = text("""
        INSERT INTO bio.taxon_compound (
            taxon_id, compound_id, relationship_type, evidence_level,
            concentration_min, concentration_max, concentration_unit,
            tissue_location, source, source_url, doi, metadata
        ) VALUES (
            :taxon_id, :compound_id, :relationship_type, :evidence_level,
            :concentration_min, :concentration_max, :concentration_unit,
            :tissue_location, :source, :source_url, :doi, :metadata
        )
        RETURNING id, created_at, updated_at
    """)
    
    try:
        result = await session.execute(query, {
            "taxon_id": link.taxon_id,
            "compound_id": link.compound_id,
            "relationship_type": link.relationship_type,
            "evidence_level": link.evidence_level,
            "concentration_min": link.concentration_min,
            "concentration_max": link.concentration_max,
            "concentration_unit": link.concentration_unit,
            "tissue_location": link.tissue_location,
            "source": link.source,
            "source_url": link.source_url,
            "doi": link.doi,
            "metadata": link.metadata,
        })
        await session.commit()
        row = result.fetchone()
        
        return TaxonCompoundResponse(
            id=row.id,
            taxon_id=link.taxon_id,
            compound_id=link.compound_id,
            relationship_type=link.relationship_type,
            evidence_level=link.evidence_level,
            concentration_min=link.concentration_min,
            concentration_max=link.concentration_max,
            concentration_unit=link.concentration_unit,
            tissue_location=link.tissue_location,
            source=link.source,
            source_url=link.source_url,
            doi=link.doi,
            metadata=link.metadata,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to link compound to taxon: {str(e)}",
        )


# =============================================================================
# CHEMSPIDER INTEGRATION ENDPOINTS
# =============================================================================

@router.post("/enrich", response_model=ChemSpiderEnrichResponse)
async def enrich_compound_from_chemspider(
    request: ChemSpiderEnrichRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Enrich a compound with data from ChemSpider.
    
    Provide one of: compound_id, compound_name, chemspider_id, smiles, or inchikey.
    """
    try:
        # Import ChemSpider client
        from mindex_etl.sources.chemspider import ChemSpiderClient, ChemSpiderAuthError
        
        # Check API key
        api_key = os.getenv("CHEMSPIDER_API_KEY")
        if not api_key:
            return ChemSpiderEnrichResponse(
                success=False,
                message="CHEMSPIDER_API_KEY not configured",
            )
        
        with ChemSpiderClient(api_key) as client:
            compound_data = None
            
            if request.chemspider_id:
                # Direct lookup by ChemSpider ID
                compound_data = client.get_compound(request.chemspider_id)
            elif request.compound_name:
                # Search by name
                results = client.search_by_name(request.compound_name, max_results=1)
                if results:
                    compound_data = results[0]
            elif request.smiles:
                # Search by SMILES
                query_id = client.filter_by_smiles(request.smiles)
                client.wait_for_filter_complete(query_id)
                record_ids = client.get_filter_results(query_id, count=1)
                if record_ids:
                    compound_data = client.get_compound(record_ids[0])
            elif request.inchikey:
                # Search by InChIKey
                query_id = client.filter_by_inchikey(request.inchikey)
                client.wait_for_filter_complete(query_id)
                record_ids = client.get_filter_results(query_id, count=1)
                if record_ids:
                    compound_data = client.get_compound(record_ids[0])
            
            if not compound_data:
                return ChemSpiderEnrichResponse(
                    success=False,
                    message="Compound not found in ChemSpider",
                )
            
            # Update or create compound in database
            enriched_fields = []
            chemspider_id = compound_data.get("id")
            
            # Check if compound exists
            if request.compound_id:
                # Update existing compound
                update_fields = []
                params = {"compound_id": request.compound_id}
                
                if compound_data.get("formula"):
                    update_fields.append("formula = :formula")
                    params["formula"] = compound_data["formula"]
                    enriched_fields.append("formula")
                
                if compound_data.get("molecularWeight"):
                    update_fields.append("molecular_weight = :molecular_weight")
                    params["molecular_weight"] = compound_data["molecularWeight"]
                    enriched_fields.append("molecular_weight")
                
                if compound_data.get("smiles"):
                    update_fields.append("smiles = :smiles")
                    params["smiles"] = compound_data["smiles"]
                    enriched_fields.append("smiles")
                
                if compound_data.get("inchi"):
                    update_fields.append("inchi = :inchi")
                    params["inchi"] = compound_data["inchi"]
                    enriched_fields.append("inchi")
                
                if compound_data.get("inchiKey"):
                    update_fields.append("inchikey = :inchikey")
                    params["inchikey"] = compound_data["inchiKey"]
                    enriched_fields.append("inchikey")
                
                if chemspider_id:
                    update_fields.append("chemspider_id = :chemspider_id")
                    params["chemspider_id"] = chemspider_id
                    enriched_fields.append("chemspider_id")
                
                if update_fields:
                    update_fields.append("updated_at = now()")
                    update_sql = f"UPDATE bio.compound SET {', '.join(update_fields)} WHERE id = :compound_id"
                    await session.execute(text(update_sql), params)
                    await session.commit()
                
                return ChemSpiderEnrichResponse(
                    success=True,
                    compound_id=request.compound_id,
                    chemspider_id=chemspider_id,
                    enriched_fields=enriched_fields,
                    message=f"Enriched {len(enriched_fields)} fields from ChemSpider",
                )
            else:
                # Create new compound
                insert_query = text("""
                    INSERT INTO bio.compound (
                        name, formula, molecular_weight, smiles, inchi, inchikey,
                        chemspider_id, source
                    ) VALUES (
                        :name, :formula, :molecular_weight, :smiles, :inchi, :inchikey,
                        :chemspider_id, 'chemspider'
                    )
                    RETURNING id
                """)
                
                result = await session.execute(insert_query, {
                    "name": compound_data.get("commonName") or request.compound_name,
                    "formula": compound_data.get("formula"),
                    "molecular_weight": compound_data.get("molecularWeight"),
                    "smiles": compound_data.get("smiles"),
                    "inchi": compound_data.get("inchi"),
                    "inchikey": compound_data.get("inchiKey"),
                    "chemspider_id": chemspider_id,
                })
                await session.commit()
                row = result.fetchone()
                
                return ChemSpiderEnrichResponse(
                    success=True,
                    compound_id=row.id,
                    chemspider_id=chemspider_id,
                    enriched_fields=["name", "formula", "molecular_weight", "smiles", "inchi", "inchikey", "chemspider_id"],
                    message="Created new compound from ChemSpider data",
                )
                
    except Exception as e:
        return ChemSpiderEnrichResponse(
            success=False,
            message=f"Error enriching from ChemSpider: {str(e)}",
        )


@router.post("/chemspider/search", response_model=ChemSpiderSearchResponse)
async def search_chemspider(
    request: ChemSpiderSearchRequest,
):
    """
    Search ChemSpider directly without saving to database.
    
    Useful for compound discovery and validation.
    """
    try:
        from mindex_etl.sources.chemspider import ChemSpiderClient, ChemSpiderAuthError
        
        api_key = os.getenv("CHEMSPIDER_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CHEMSPIDER_API_KEY not configured",
            )
        
        with ChemSpiderClient(api_key) as client:
            # Perform search based on type
            if request.search_type == "name":
                results = client.search_by_name(request.query, max_results=request.max_results)
            elif request.search_type == "formula":
                results = client.search_by_formula(request.query, max_results=request.max_results)
            elif request.search_type == "smiles":
                query_id = client.filter_by_smiles(request.query)
                client.wait_for_filter_complete(query_id)
                record_ids = client.get_filter_results(query_id, count=request.max_results)
                results = client.get_batch_compounds(record_ids) if record_ids else []
            elif request.search_type == "inchi":
                query_id = client.filter_by_inchi(request.query)
                client.wait_for_filter_complete(query_id)
                record_ids = client.get_filter_results(query_id, count=request.max_results)
                results = client.get_batch_compounds(record_ids) if record_ids else []
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid search_type: {request.search_type}",
                )
            
            search_results = [
                ChemSpiderSearchResult(
                    chemspider_id=r.get("id"),
                    name=r.get("commonName") or r.get("name"),
                    formula=r.get("formula"),
                    molecular_weight=r.get("molecularWeight"),
                    smiles=r.get("smiles"),
                    inchikey=r.get("inchiKey"),
                )
                for r in results
            ]
            
            return ChemSpiderSearchResponse(
                query=request.query,
                search_type=request.search_type,
                results=search_results,
                total_count=len(search_results),
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ChemSpider search failed: {str(e)}",
        )


# =============================================================================
# BIOLOGICAL ACTIVITY ENDPOINTS
# =============================================================================

@router.get("/activities", response_model=List[BiologicalActivityResponse])
async def list_biological_activities(
    session: AsyncSession = Depends(get_db_session),
):
    """List all available biological activities."""
    query = text("SELECT id, name, category, description FROM bio.biological_activity ORDER BY category, name")
    result = await session.execute(query)
    
    return [
        BiologicalActivityResponse(
            id=row.id,
            name=row.name,
            category=row.category,
            description=row.description,
        )
        for row in result.fetchall()
    ]
