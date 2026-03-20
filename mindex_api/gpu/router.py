"""
GPU Router — Internal API Endpoints
======================================
Internal zone endpoints for GPU-accelerated operations. Protected by
X-Internal-Token auth. All endpoints degrade gracefully when GPU is
unavailable.

Endpoints:
    POST /gpu/search         — cuVS vector similarity search
    POST /gpu/index/build    — build cuVS index from pgvector source
    POST /gpu/index/update   — add vectors to existing index
    GET  /gpu/index/{name}   — index status and stats
    GET  /gpu/indexes        — list all indexes
    POST /gpu/verify         — Merkle proof verification
    GET  /gpu/health         — GPU device status
    POST /gpu/static/extract — extract STATIC constraints from domain
    POST /gpu/static/decode  — constrained decoding via MAS STATIC
    GET  /gpu/static/domains — list cached constraint domains
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

gpu_router = APIRouter(prefix="/gpu", tags=["GPU Acceleration"])


# =========================================================================
# Request/Response Models
# =========================================================================


class VectorSearchRequest(BaseModel):
    index_name: str = Field(..., description="Name of the cuVS index to search")
    query_vector: List[float] = Field(..., description="Query embedding vector")
    k: int = Field(10, ge=1, le=1000, description="Number of nearest neighbors")
    include_proof: bool = Field(False, description="Include MICA Merkle proof reference")


class VectorSearchResponse(BaseModel):
    results: List[Dict[str, Any]]
    index_name: str
    backend: str  # "cuvs", "numpy", or "pgvector"
    vector_count: int
    latency_ms: float
    root_hash: Optional[str] = None  # MICA root hash if include_proof=True


class IndexBuildRequest(BaseModel):
    index_name: str = Field(..., description="Index to build (fci_signals, nlm_nature, image_similarity)")
    record_mica: bool = Field(True, description="Record index state in MICA Merkle ledger")


class IndexBuildResponse(BaseModel):
    index_name: str
    status: str
    vector_count: int
    dimensions: int
    index_type: str
    build_time_seconds: float
    root_hash: Optional[str] = None


class IndexUpdateRequest(BaseModel):
    index_name: str
    vectors: List[List[float]]
    ids: List[str]


class VerifyRequest(BaseModel):
    root_hash: str = Field(..., description="Hex-encoded Merkle root hash")
    member_hash: str = Field(..., description="Hex-encoded member hash to verify")


class StaticExtractRequest(BaseModel):
    domain: str = Field(..., description="Constraint domain (taxonomy, compounds, devices, etc.)")
    limit: Optional[int] = Field(None, description="Max constraints to extract")
    push_to_mas: bool = Field(False, description="Push constraint set to MAS for STATIC index building")


class StaticDecodeRequest(BaseModel):
    prompt: str = Field(..., description="Input prompt for constrained decoding")
    constraint_domain: str = Field(..., description="Domain for constraint masking")
    max_tokens: int = Field(64, ge=1, le=512)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    top_k: int = Field(10, ge=1, le=100)


# =========================================================================
# GPU Health
# =========================================================================


@gpu_router.get("/health")
async def gpu_health() -> Dict[str, Any]:
    """GPU device status, VRAM, and library availability."""
    from .runtime import get_gpu_runtime
    from . import get_availability

    runtime = get_gpu_runtime()
    status = runtime.health_status()
    status.update(get_availability())
    return status


# =========================================================================
# Vector Search
# =========================================================================


@gpu_router.post("/search", response_model=VectorSearchResponse)
async def vector_search(
    request: VectorSearchRequest,
    db: AsyncSession = Depends(get_db_session),
) -> VectorSearchResponse:
    """GPU-accelerated vector similarity search via cuVS.

    Falls back to NumPy brute-force or pgvector SQL when GPU is unavailable.
    """
    import time

    from .cuvs_index import get_index_manager

    manager = get_index_manager()
    t0 = time.monotonic()

    results = await manager.search(
        index_name=request.index_name,
        query_vector=request.query_vector,
        k=request.k,
        db_session=db,
    )

    elapsed_ms = (time.monotonic() - t0) * 1000

    # Determine backend used
    status = manager.get_status(request.index_name)
    if status and status.is_loaded:
        from . import CUVS_AVAILABLE

        backend = "cuvs" if CUVS_AVAILABLE else "numpy"
        vector_count = status.vector_count
    else:
        backend = "pgvector"
        vector_count = 0

    response = VectorSearchResponse(
        results=[
            {"id": r.id, "distance": r.distance, "rank": r.rank}
            for r in results
        ],
        index_name=request.index_name,
        backend=backend,
        vector_count=vector_count,
        latency_ms=round(elapsed_ms, 2),
    )

    # Include MICA proof reference if requested
    if request.include_proof:
        from .mica_bridge import get_mica_bridge

        mica = get_mica_bridge()
        chain = await mica.get_root_chain(db, f"cuvs.{request.index_name}", max_depth=1)
        if chain:
            response.root_hash = chain[0]["root_hash"]

    return response


# =========================================================================
# Index Management
# =========================================================================


@gpu_router.post("/index/build", response_model=IndexBuildResponse)
async def build_index(
    request: IndexBuildRequest,
    db: AsyncSession = Depends(get_db_session),
) -> IndexBuildResponse:
    """Build or rebuild a cuVS vector index from pgvector source table."""
    from .cuvs_index import get_index_manager

    manager = get_index_manager()
    status = await manager.build_index(request.index_name, db)

    root_hash = None
    if request.record_mica and status.vector_count > 0:
        from .mica_bridge import get_mica_bridge

        mica = get_mica_bridge()
        index_hash = manager.compute_index_hash(request.index_name)
        if index_hash:
            root = await mica.record_index_snapshot(
                db, request.index_name, index_hash, status.vector_count,
            )
            root_hash = root.hex()

    return IndexBuildResponse(
        index_name=status.name,
        status="ready" if status.is_loaded else "error",
        vector_count=status.vector_count,
        dimensions=status.dimensions,
        index_type=status.index_type,
        build_time_seconds=round(status.last_build_time or 0, 3),
        root_hash=root_hash,
    )


@gpu_router.post("/index/update")
async def update_index(
    request: IndexUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Add vectors to an existing index without full rebuild."""
    from .cuvs_index import get_index_manager

    if len(request.vectors) != len(request.ids):
        raise HTTPException(400, "vectors and ids must have the same length")

    manager = get_index_manager()
    added = await manager.add_vectors(request.index_name, request.vectors, request.ids)

    return {
        "index_name": request.index_name,
        "vectors_added": added,
        "total_vectors": manager.get_status(request.index_name).vector_count
        if manager.get_status(request.index_name) else 0,
    }


@gpu_router.get("/index/{name}")
async def index_status(name: str) -> Dict[str, Any]:
    """Get status of a specific index."""
    from .cuvs_index import get_index_manager

    manager = get_index_manager()
    status = manager.get_status(name)

    if status is None:
        available = manager.get_available_indexes()
        raise HTTPException(
            404,
            f"Index '{name}' not loaded. Available indexes: {available}. "
            f"Use POST /gpu/index/build to build it.",
        )

    return {
        "name": status.name,
        "dimensions": status.dimensions,
        "index_type": status.index_type,
        "vector_count": status.vector_count,
        "is_loaded": status.is_loaded,
        "last_build_time": status.last_build_time,
        "storage_path": status.storage_path,
    }


@gpu_router.get("/indexes")
async def list_indexes() -> Dict[str, Any]:
    """List all available and loaded indexes."""
    from .cuvs_index import get_index_manager

    manager = get_index_manager()

    return {
        "available": manager.get_available_indexes(),
        "loaded": {
            name: {
                "dimensions": s.dimensions,
                "vector_count": s.vector_count,
                "index_type": s.index_type,
                "is_loaded": s.is_loaded,
            }
            for name, s in manager.list_indexes().items()
        },
    }


# =========================================================================
# Merkle Verification
# =========================================================================


@gpu_router.post("/verify")
async def verify_proof(
    request: VerifyRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Verify that a member exists in a MICA Merkle root."""
    from .mica_bridge import get_mica_bridge

    mica = get_mica_bridge()
    root_hash = bytes.fromhex(request.root_hash)
    member_hash = bytes.fromhex(request.member_hash)

    is_member = await mica.verify_membership(db, root_hash, member_hash)

    return {
        "root_hash": request.root_hash,
        "member_hash": request.member_hash,
        "is_member": is_member,
        "verified": is_member,
    }


@gpu_router.get("/merkle/chain/{head_name}")
async def get_merkle_chain(
    head_name: str,
    depth: int = 10,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Walk the Merkle root chain for a mutable head."""
    from .mica_bridge import get_mica_bridge

    mica = get_mica_bridge()
    chain = await mica.get_root_chain(db, head_name, max_depth=depth)

    return {
        "head_name": head_name,
        "chain_length": len(chain),
        "roots": chain,
    }


# =========================================================================
# STATIC Constrained Decoding
# =========================================================================


@gpu_router.post("/static/extract")
async def extract_constraints(
    request: StaticExtractRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Extract STATIC constraints from a MINDEX data domain.

    Queries the database for all valid identifiers in the specified domain
    and builds a constraint set for STATIC constrained decoding.
    """
    from .static_bridge import ConstraintDomain, get_static_bridge

    try:
        domain = ConstraintDomain(request.domain)
    except ValueError:
        raise HTTPException(
            400,
            f"Unknown domain: {request.domain}. "
            f"Valid domains: {[d.value for d in ConstraintDomain]}",
        )

    bridge = get_static_bridge()
    constraint_set = await bridge.extract_constraints(domain, db, limit=request.limit)

    # Optionally push to MAS
    pushed = False
    if request.push_to_mas:
        pushed = await bridge.push_constraints_to_mas(constraint_set)

    # Record in MICA
    from .mica_bridge import get_mica_bridge

    mica = get_mica_bridge()
    await mica.record_event(
        db,
        kind="gpu.constraint_set",
        payload={
            "domain": request.domain,
            "sequence_count": constraint_set.sequence_count,
            "version_hash": constraint_set.version_hash,
            "pushed_to_mas": pushed,
        },
    )

    return {
        "domain": request.domain,
        "sequence_count": constraint_set.sequence_count,
        "version_hash": constraint_set.version_hash,
        "pushed_to_mas": pushed,
        "sample": constraint_set.sequences[:10],  # First 10 as preview
    }


@gpu_router.post("/static/decode")
async def constrained_decode(
    request: StaticDecodeRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Forward constrained decoding request to MAS STATIC.

    MAS applies STATIC CSR matrix masks during autoregressive decoding
    to constrain LLM output to valid MINDEX entities.
    """
    from .static_bridge import ConstraintDomain, ConstrainedDecodingRequest, get_static_bridge

    bridge = get_static_bridge()
    if not bridge.enabled:
        raise HTTPException(
            503,
            "STATIC not enabled. Set STATIC_ENABLED=true and STATIC_MAS_ENDPOINT.",
        )

    # Get cached constraint set hash
    try:
        domain = ConstraintDomain(request.constraint_domain)
    except ValueError:
        raise HTTPException(400, f"Unknown domain: {request.constraint_domain}")

    cached = bridge.get_cached_constraints(domain)
    if cached is None:
        # Auto-extract if not cached
        cached = await bridge.extract_constraints(domain, db)

    decode_request = ConstrainedDecodingRequest(
        prompt=request.prompt,
        constraint_domain=domain,
        constraint_set_hash=cached.version_hash,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        top_k=request.top_k,
    )

    result = await bridge.constrained_decode(decode_request)
    if result is None:
        raise HTTPException(502, "Constrained decoding failed — MAS unreachable")

    return {
        "sequences": result.sequences,
        "scores": result.scores,
        "constraint_domain": result.constraint_domain,
        "constraint_set_hash": result.constraint_set_hash,
        "latency_ms": round(result.latency_ms, 2),
        "model_id": result.model_id,
    }


@gpu_router.get("/static/domains")
async def list_static_domains() -> Dict[str, Any]:
    """List all cached STATIC constraint domains."""
    from .static_bridge import ConstraintDomain, get_static_bridge

    bridge = get_static_bridge()

    return {
        "enabled": bridge.enabled,
        "available_domains": [d.value for d in ConstraintDomain],
        "cached": bridge.list_cached_domains(),
    }
