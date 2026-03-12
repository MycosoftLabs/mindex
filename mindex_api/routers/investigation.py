"""
Investigation Router - March 10, 2026

OpenPlanter-style investigation artifacts and evidence-backed analysis.
Per INTEGRATION_CONTRACTS_CANONICAL_MAR10_2026.

Endpoints:
- POST /investigation/artifacts - Create investigation artifact
- GET /investigation/artifacts - List/query artifacts
- GET /investigation/artifacts/{id} - Get artifact by id
- POST /investigation/evidence - Create evidence relationship
- GET /investigation/evidence - Query evidence by entity
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import PaginationParams, get_db_session, pagination_params, require_api_key

router = APIRouter(
    prefix="/investigation",
    tags=["investigation"],
    dependencies=[Depends(require_api_key)],
)


# --- Schemas ---


class InvestigationArtifactCreate(BaseModel):
    """Create investigation artifact."""
    title: str
    description: Optional[str] = None
    source: str = Field(..., description="e.g. openalex, inat, mindex, observation")
    source_id: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    agent_id: Optional[str] = None


class InvestigationArtifactOut(BaseModel):
    """Investigation artifact response."""
    id: UUID
    title: str
    description: Optional[str]
    source: str
    source_id: Optional[str]
    artifacts: List[Any]
    sources: List[Any]
    agent_id: Optional[str]
    created_at: datetime
    updated_at: datetime


class EvidenceRelationshipCreate(BaseModel):
    """Create evidence relationship."""
    entity_id: str
    evidence_ids: List[str] = Field(default_factory=list)
    relationship_type: str = Field(..., description="e.g. supports, contradicts, cites")
    confidence: Optional[float] = None


class EvidenceRelationshipOut(BaseModel):
    """Evidence relationship response."""
    id: UUID
    entity_id: str
    evidence_ids: List[str]
    relationship_type: str
    confidence: Optional[float]
    created_at: datetime


# --- Artifacts ---


@router.post("/artifacts", response_model=InvestigationArtifactOut)
async def create_artifact(
    body: InvestigationArtifactCreate,
    db: AsyncSession = Depends(get_db_session),
) -> InvestigationArtifactOut:
    """Create an investigation artifact (research paper, observation, etc.)."""
    import json
    artifacts_json = json.dumps(body.artifacts)
    sources_json = json.dumps(body.sources)
    stmt = text(
        """
        INSERT INTO investigation.investigation_artifacts
        (title, description, source, source_id, artifacts, sources, agent_id)
        VALUES (:title, :description, :source, :source_id, :artifacts::jsonb, :sources::jsonb, :agent_id)
        RETURNING id, title, description, source, source_id, artifacts, sources, agent_id, created_at, updated_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "title": body.title,
            "description": body.description,
            "source": body.source,
            "source_id": body.source_id,
            "artifacts": artifacts_json,
            "sources": sources_json,
            "agent_id": body.agent_id,
        },
    )
    row = result.fetchone()
    await db.commit()
    return InvestigationArtifactOut(
        id=row[0],
        title=row[1],
        description=row[2],
        source=row[3],
        source_id=row[4],
        artifacts=row[5] or [],
        sources=row[6] or [],
        agent_id=row[7],
        created_at=row[8],
        updated_at=row[9],
    )


@router.get("/artifacts", response_model=List[InvestigationArtifactOut])
async def list_artifacts(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    source: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
) -> List[InvestigationArtifactOut]:
    """List investigation artifacts with optional filters."""
    where = []
    params: dict = {"limit": pagination.limit, "offset": pagination.offset}
    if source:
        where.append("source = :source")
        params["source"] = source
    if agent_id:
        where.append("agent_id = :agent_id")
        params["agent_id"] = agent_id
    where_sql = " AND ".join(where) if where else "TRUE"
    stmt = text(
        f"""
        SELECT id, title, description, source, source_id, artifacts, sources, agent_id, created_at, updated_at
        FROM investigation.investigation_artifacts
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    result = await db.execute(stmt, params)
    return [
        InvestigationArtifactOut(
            id=row[0],
            title=row[1],
            description=row[2],
            source=row[3],
            source_id=row[4],
            artifacts=row[5] or [],
            sources=row[6] or [],
            agent_id=row[7],
            created_at=row[8],
            updated_at=row[9],
        )
        for row in result.fetchall()
    ]


@router.get("/artifacts/{artifact_id}", response_model=InvestigationArtifactOut)
async def get_artifact(
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> InvestigationArtifactOut:
    """Get investigation artifact by id."""
    stmt = text(
        """
        SELECT id, title, description, source, source_id, artifacts, sources, agent_id, created_at, updated_at
        FROM investigation.investigation_artifacts
        WHERE id = :id
        """
    )
    result = await db.execute(stmt, {"id": str(artifact_id)})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return InvestigationArtifactOut(
        id=row[0],
        title=row[1],
        description=row[2],
        source=row[3],
        source_id=row[4],
        artifacts=row[5] or [],
        sources=row[6] or [],
        agent_id=row[7],
        created_at=row[8],
        updated_at=row[9],
    )


# --- Evidence relationships ---


@router.post("/evidence", response_model=EvidenceRelationshipOut)
async def create_evidence_relationship(
    body: EvidenceRelationshipCreate,
    db: AsyncSession = Depends(get_db_session),
) -> EvidenceRelationshipOut:
    """Create an evidence relationship linking entity to evidence artifacts."""
    import json
    evidence_json = json.dumps(body.evidence_ids)
    stmt = text(
        """
        INSERT INTO investigation.evidence_relationships
        (entity_id, evidence_ids, relationship_type, confidence)
        VALUES (:entity_id, :evidence_ids::jsonb, :relationship_type, :confidence)
        RETURNING id, entity_id, evidence_ids, relationship_type, confidence, created_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "entity_id": body.entity_id,
            "evidence_ids": evidence_json,
            "relationship_type": body.relationship_type,
            "confidence": body.confidence,
        },
    )
    row = result.fetchone()
    await db.commit()
    return EvidenceRelationshipOut(
        id=row[0],
        entity_id=row[1],
        evidence_ids=row[2] or [],
        relationship_type=row[3],
        confidence=row[4],
        created_at=row[5],
    )


@router.get("/evidence", response_model=List[EvidenceRelationshipOut])
async def list_evidence_by_entity(
    entity_id: str = Query(..., description="Entity id to look up"),
    relationship_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
) -> List[EvidenceRelationshipOut]:
    """List evidence relationships for an entity."""
    params: dict = {"entity_id": entity_id}
    where = "entity_id = :entity_id"
    if relationship_type:
        where += " AND relationship_type = :relationship_type"
        params["relationship_type"] = relationship_type
    stmt = text(
        f"""
        SELECT id, entity_id, evidence_ids, relationship_type, confidence, created_at
        FROM investigation.evidence_relationships
        WHERE {where}
        ORDER BY created_at DESC
        """
    )
    result = await db.execute(stmt, params)
    return [
        EvidenceRelationshipOut(
            id=row[0],
            entity_id=row[1],
            evidence_ids=row[2] or [],
            relationship_type=row[3],
            confidence=row[4],
            created_at=row[5],
        )
        for row in result.fetchall()
    ]
