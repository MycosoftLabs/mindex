"""
Genetics Router

API endpoints for genetic sequence data (GenBank, NCBI, etc.).
On-demand ingest: when detail is requested for an accession not in MINDEX,
fetch from GenBank and store so the user stays in-app.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, pagination_params, require_api_key, PaginationParams


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class GeneticSequenceResponse(BaseModel):
    """Response model for a genetic sequence."""
    id: int
    accession: str
    species_name: Optional[str] = None
    gene: Optional[str] = None
    region: Optional[str] = None
    sequence: str
    sequence_length: int
    sequence_type: Optional[str] = "dna"
    source: str
    source_url: Optional[str] = None
    definition: Optional[str] = None
    organism: Optional[str] = None
    pubmed_id: Optional[int] = None
    doi: Optional[str] = None

    class Config:
        from_attributes = True


class GeneticSequenceListResponse(BaseModel):
    """Paginated list of genetic sequences."""
    data: List[GeneticSequenceResponse]
    pagination: dict = Field(default_factory=dict)


class GeneticSequenceCreate(BaseModel):
    """Request model for creating a genetic sequence."""
    accession: str
    species_name: Optional[str] = None
    gene: Optional[str] = None
    region: Optional[str] = None
    sequence: str
    sequence_type: Optional[str] = "dna"
    source: str = "genbank"
    source_url: Optional[str] = None
    definition: Optional[str] = None
    organism: Optional[str] = None
    pubmed_id: Optional[int] = None
    doi: Optional[str] = None


class GeneStatsResponse(BaseModel):
    """Statistics for sequences by gene."""
    gene: str
    sequence_count: int
    species_count: int
    avg_length: int
    min_length: int
    max_length: int


class IngestAccessionRequest(BaseModel):
    """Request to ingest a GenBank record by accession if not already in MINDEX."""
    accession: str = Field(..., min_length=1, description="GenBank accession (e.g. AF123456)")


# =============================================================================
# ROUTER
# =============================================================================

router = APIRouter(
    prefix="/genetics",
    tags=["Genetics"],
    dependencies=[Depends(require_api_key)],
)

async def _genetic_sequence_table_exists(db: AsyncSession) -> bool:
    """
    Return True if bio.genetic_sequence exists.

    MINDEX environments can drift; this prevents 500s when the genetics
    schema/migrations have not been applied yet.
    """
    result = await db.execute(text("SELECT to_regclass('bio.genetic_sequence')"))
    return result.scalar_one_or_none() is not None


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("", response_model=GeneticSequenceListResponse)
async def list_genetic_sequences(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    search: Optional[str] = Query(None, description="Search in species name, gene, or accession"),
    gene: Optional[str] = Query(None, description="Filter by gene (e.g., ITS, LSU, RPB1)"),
    source: Optional[str] = Query(None, description="Filter by source (e.g., genbank, ncbi)"),
    species: Optional[str] = Query(None, description="Filter by species name"),
    min_length: Optional[int] = Query(None, ge=1, description="Minimum sequence length"),
    max_length: Optional[int] = Query(None, ge=1, description="Maximum sequence length"),
) -> GeneticSequenceListResponse:
    """
    List genetic sequences with optional filtering.
    
    - **search**: Free-text search across species name, gene, and accession
    - **gene**: Filter by specific gene (ITS, LSU, SSU, RPB1, RPB2, TEF1, etc.)
    - **source**: Filter by data source (genbank, ncbi, bold, unite)
    - **species**: Filter by species name (partial match)
    - **min_length/max_length**: Filter by sequence length range
    """
    if not await _genetic_sequence_table_exists(db):
        return GeneticSequenceListResponse(
            data=[],
            pagination={
                "limit": pagination.limit,
                "offset": pagination.offset,
                "total": 0,
            },
        )

    # Build dynamic WHERE clause
    where_clauses = []
    params: dict = {
        "limit": pagination.limit,
        "offset": pagination.offset,
    }
    
    if search:
        search_pattern = f"%{search}%"
        where_clauses.append(
            "(species_name ILIKE :search OR gene ILIKE :search OR accession ILIKE :search)"
        )
        params["search"] = search_pattern
    
    if gene:
        where_clauses.append("gene = :gene")
        params["gene"] = gene
    
    if source:
        where_clauses.append("source = :source")
        params["source"] = source
    
    if species:
        species_pattern = f"%{species}%"
        where_clauses.append("species_name ILIKE :species")
        params["species"] = species_pattern
    
    if min_length:
        where_clauses.append("sequence_length >= :min_length")
        params["min_length"] = min_length
    
    if max_length:
        where_clauses.append("sequence_length <= :max_length")
        params["max_length"] = max_length
    
    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
    
    # Count query
    count_stmt = text(f"SELECT COUNT(*) FROM bio.genetic_sequence WHERE {where_sql}")
    count_result = await db.execute(count_stmt, params)
    total = count_result.scalar_one()
    
    # Data query
    stmt = text(f"""
        SELECT
            id,
            accession,
            species_name,
            gene,
            region,
            sequence,
            sequence_length,
            sequence_type,
            source,
            source_url,
            definition,
            organism,
            pubmed_id,
            doi
        FROM bio.genetic_sequence
        WHERE {where_sql}
        ORDER BY species_name NULLS LAST, gene, accession
        LIMIT :limit OFFSET :offset
    """)
    
    result = await db.execute(stmt, params)
    rows = result.mappings().all()
    
    sequences = [
        GeneticSequenceResponse(
            id=row["id"],
            accession=row["accession"],
            species_name=row["species_name"],
            gene=row["gene"],
            region=row["region"],
            sequence=row["sequence"],
            sequence_length=row["sequence_length"],
            sequence_type=row["sequence_type"],
            source=row["source"],
            source_url=row["source_url"],
            definition=row["definition"],
            organism=row["organism"],
            pubmed_id=row["pubmed_id"],
            doi=row["doi"],
        )
        for row in rows
    ]
    
    return GeneticSequenceListResponse(
        data=sequences,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )


@router.get("/stats", response_model=List[GeneStatsResponse])
async def get_gene_statistics(
    db: AsyncSession = Depends(get_db_session),
) -> List[GeneStatsResponse]:
    """
    Get sequence statistics grouped by gene.
    
    Returns count of sequences, species, and length statistics for each gene.
    """
    if not await _genetic_sequence_table_exists(db):
        return []

    stmt = text("""
        SELECT
            gene,
            COUNT(*) AS sequence_count,
            COUNT(DISTINCT species_name) AS species_count,
            AVG(sequence_length)::INTEGER AS avg_length,
            MIN(sequence_length) AS min_length,
            MAX(sequence_length) AS max_length
        FROM bio.genetic_sequence
        WHERE gene IS NOT NULL
        GROUP BY gene
        ORDER BY sequence_count DESC
    """)
    
    result = await db.execute(stmt)
    rows = result.mappings().all()
    
    return [
        GeneStatsResponse(
            gene=row["gene"],
            sequence_count=row["sequence_count"],
            species_count=row["species_count"],
            avg_length=row["avg_length"] or 0,
            min_length=row["min_length"] or 0,
            max_length=row["max_length"] or 0,
        )
        for row in rows
    ]


@router.get("/{sequence_id}", response_model=GeneticSequenceResponse)
async def get_genetic_sequence(
    sequence_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> GeneticSequenceResponse:
    """Get a single genetic sequence by ID."""
    if not await _genetic_sequence_table_exists(db):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Genetic sequences are not available in this environment",
        )

    stmt = text("""
        SELECT
            id,
            accession,
            species_name,
            gene,
            region,
            sequence,
            sequence_length,
            sequence_type,
            source,
            source_url,
            definition,
            organism,
            pubmed_id,
            doi
        FROM bio.genetic_sequence
        WHERE id = :sequence_id
    """)
    
    result = await db.execute(stmt, {"sequence_id": sequence_id})
    row = result.mappings().one_or_none()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Genetic sequence {sequence_id} not found",
        )
    
    return GeneticSequenceResponse(
        id=row["id"],
        accession=row["accession"],
        species_name=row["species_name"],
        gene=row["gene"],
        region=row["region"],
        sequence=row["sequence"],
        sequence_length=row["sequence_length"],
        sequence_type=row["sequence_type"],
        source=row["source"],
        source_url=row["source_url"],
        definition=row["definition"],
        organism=row["organism"],
        pubmed_id=row["pubmed_id"],
        doi=row["doi"],
    )


@router.get("/accession/{accession}", response_model=GeneticSequenceResponse)
async def get_sequence_by_accession(
    accession: str,
    db: AsyncSession = Depends(get_db_session),
) -> GeneticSequenceResponse:
    """Get a genetic sequence by its accession number."""
    stmt = text("""
        SELECT
            id,
            accession,
            species_name,
            gene,
            region,
            sequence,
            sequence_length,
            sequence_type,
            source,
            source_url,
            definition,
            organism,
            pubmed_id,
            doi
        FROM bio.genetic_sequence
        WHERE accession = :accession
    """)
    
    result = await db.execute(stmt, {"accession": accession})
    row = result.mappings().one_or_none()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Genetic sequence with accession '{accession}' not found",
        )
    
    return GeneticSequenceResponse(
        id=row["id"],
        accession=row["accession"],
        species_name=row["species_name"],
        gene=row["gene"],
        region=row["region"],
        sequence=row["sequence"],
        sequence_length=row["sequence_length"],
        sequence_type=row["sequence_type"],
        source=row["source"],
        source_url=row["source_url"],
        definition=row["definition"],
        organism=row["organism"],
        pubmed_id=row["pubmed_id"],
        doi=row["doi"],
    )


@router.post("/ingest-accession", response_model=GeneticSequenceResponse)
async def ingest_accession(
    body: IngestAccessionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> GeneticSequenceResponse:
    """
    Ensure a GenBank record is in MINDEX by accession.
    If already present, returns it. If not, fetches from NCBI GenBank, stores in MINDEX, and returns.
    Keeps users in-app: no need to open GenBank externally.
    """
    if not await _genetic_sequence_table_exists(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Genetic sequences table not available",
        )
    accession = body.accession.strip()
    # Already in DB?
    stmt = text("""
        SELECT id, accession, species_name, gene, region, sequence, sequence_length,
               sequence_type, source, source_url, definition, organism, pubmed_id, doi
        FROM bio.genetic_sequence WHERE accession = :accession
    """)
    result = await db.execute(stmt, {"accession": accession})
    row = result.mappings().one_or_none()
    if row:
        return GeneticSequenceResponse(
            id=row["id"],
            accession=row["accession"],
            species_name=row["species_name"],
            gene=row["gene"],
            region=row["region"],
            sequence=row["sequence"],
            sequence_length=row["sequence_length"],
            sequence_type=row["sequence_type"],
            source=row["source"],
            source_url=row["source_url"],
            definition=row["definition"],
            organism=row["organism"],
            pubmed_id=row["pubmed_id"],
            doi=row["doi"],
        )
    # Fetch from GenBank (sync call in thread)
    try:
        from mindex_etl.sources.genbank import fetch_record_by_accession
        genome = await asyncio.to_thread(fetch_record_by_accession, accession)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch from GenBank: {e!s}",
        )
    if not genome:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GenBank accession '{accession}' not found",
        )
    seq = genome.get("sequence") or ""
    sequence_length = len(seq.replace(" ", "").replace("\n", "")) or genome.get("sequence_length") or 0
    source_url = f"https://www.ncbi.nlm.nih.gov/nuccore/{accession}"
    insert_stmt = text("""
        INSERT INTO bio.genetic_sequence (
            accession, species_name, gene, region, sequence, sequence_length,
            sequence_type, source, source_url, definition, organism, taxonomy, metadata
        ) VALUES (
            :accession, :species_name, :gene, :region, :sequence, :sequence_length,
            :sequence_type, :source, :source_url, :definition, :organism, :taxonomy, :metadata::jsonb
        )
        RETURNING id, accession, species_name, gene, region, sequence, sequence_length,
                  sequence_type, source, source_url, definition, organism, pubmed_id, doi
    """)
    metadata = genome.get("metadata") or {}
    taxonomy = metadata.get("taxonomy") or ""
    try:
        result = await db.execute(insert_stmt, {
            "accession": accession,
            "species_name": genome.get("organism") or "",
            "gene": None,
            "region": None,
            "sequence": seq,
            "sequence_length": sequence_length,
            "sequence_type": (genome.get("molecule_type") or "dna").lower()[:20],
            "source": "genbank",
            "source_url": source_url,
            "definition": genome.get("definition") or "",
            "organism": genome.get("organism") or "",
            "taxonomy": taxonomy,
            "metadata": json.dumps(metadata),
        })
        await db.commit()
        row = result.mappings().one()
        return GeneticSequenceResponse(
            id=row["id"],
            accession=row["accession"],
            species_name=row["species_name"],
            gene=row["gene"],
            region=row["region"],
            sequence=row["sequence"],
            sequence_length=row["sequence_length"],
            sequence_type=row["sequence_type"],
            source=row["source"],
            source_url=row["source_url"],
            definition=row["definition"],
            organism=row["organism"],
            pubmed_id=row["pubmed_id"],
            doi=row["doi"],
        )
    except Exception as e:
        await db.rollback()
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            # Raced with another request; fetch and return
            result = await db.execute(stmt, {"accession": accession})
            row = result.mappings().one_or_none()
            if row:
                return GeneticSequenceResponse(
                    id=row["id"],
                    accession=row["accession"],
                    species_name=row["species_name"],
                    gene=row["gene"],
                    region=row["region"],
                    sequence=row["sequence"],
                    sequence_length=row["sequence_length"],
                    sequence_type=row["sequence_type"],
                    source=row["source"],
                    source_url=row["source_url"],
                    definition=row["definition"],
                    organism=row["organism"],
                    pubmed_id=row["pubmed_id"],
                    doi=row["doi"],
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store sequence: {e!s}",
        )


@router.post("", response_model=GeneticSequenceResponse, status_code=status.HTTP_201_CREATED)
async def create_genetic_sequence(
    sequence_data: GeneticSequenceCreate,
    db: AsyncSession = Depends(get_db_session),
) -> GeneticSequenceResponse:
    """
    Create a new genetic sequence record.
    
    The sequence_length is automatically calculated from the sequence.
    """
    sequence_length = len(sequence_data.sequence.replace(" ", "").replace("\n", ""))
    
    stmt = text("""
        INSERT INTO bio.genetic_sequence (
            accession, species_name, gene, region, sequence, sequence_length,
            sequence_type, source, source_url, definition, organism, pubmed_id, doi
        ) VALUES (
            :accession, :species_name, :gene, :region, :sequence, :sequence_length,
            :sequence_type, :source, :source_url, :definition, :organism, :pubmed_id, :doi
        )
        RETURNING id, accession, species_name, gene, region, sequence, sequence_length,
                  sequence_type, source, source_url, definition, organism, pubmed_id, doi
    """)
    
    try:
        result = await db.execute(stmt, {
            "accession": sequence_data.accession,
            "species_name": sequence_data.species_name,
            "gene": sequence_data.gene,
            "region": sequence_data.region,
            "sequence": sequence_data.sequence,
            "sequence_length": sequence_length,
            "sequence_type": sequence_data.sequence_type,
            "source": sequence_data.source,
            "source_url": sequence_data.source_url,
            "definition": sequence_data.definition,
            "organism": sequence_data.organism,
            "pubmed_id": sequence_data.pubmed_id,
            "doi": sequence_data.doi,
        })
        await db.commit()
        row = result.mappings().one()
        
        return GeneticSequenceResponse(
            id=row["id"],
            accession=row["accession"],
            species_name=row["species_name"],
            gene=row["gene"],
            region=row["region"],
            sequence=row["sequence"],
            sequence_length=row["sequence_length"],
            sequence_type=row["sequence_type"],
            source=row["source"],
            source_url=row["source_url"],
            definition=row["definition"],
            organism=row["organism"],
            pubmed_id=row["pubmed_id"],
            doi=row["doi"],
        )
    except Exception as e:
        await db.rollback()
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Sequence with accession '{sequence_data.accession}' already exists",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create sequence: {str(e)}",
        )


@router.delete("/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_genetic_sequence(
    sequence_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a genetic sequence by ID."""
    # Check if exists
    check_stmt = text("SELECT id FROM bio.genetic_sequence WHERE id = :sequence_id")
    check_result = await db.execute(check_stmt, {"sequence_id": sequence_id})
    if not check_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Genetic sequence {sequence_id} not found",
        )
    
    # Delete
    delete_stmt = text("DELETE FROM bio.genetic_sequence WHERE id = :sequence_id")
    await db.execute(delete_stmt, {"sequence_id": sequence_id})
    await db.commit()
