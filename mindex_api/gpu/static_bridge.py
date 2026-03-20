"""
STATIC Bridge — Constrained Decoding Integration
====================================================
Interfaces MINDEX with STATIC (Sparse Transition-Accelerated Trie Index for
Constrained decoding) running in MAS.

STATIC enables LLM-based generative retrieval over MINDEX data by constraining
autoregressive decoding to valid token sequences (e.g., valid species names,
compound identifiers, or Semantic IDs from the taxonomy tree).

Architecture:
    MINDEX (constraint source) ←→ MAS (STATIC decoder) ←→ LLM inference

    1. MINDEX exports constraint sets (valid sequences) from its data:
       - Taxonomic names (core.taxon canonical_name)
       - Compound identifiers (bio.compound name, inchikey)
       - Device IDs (telemetry.device device_id)
       - Observation IDs (obs.observation source_id)
       - Earth event types (earth.* categories)
       - MICA object hashes (mica.ca_object object_hash)

    2. STATIC (in MAS) builds CSR sparse matrix index offline

    3. At inference time, STATIC applies masks to LLM logits in O(1)
       per step, constraining output to valid MINDEX entities

    4. Results are Merkle-verified via MICA before being returned

Interfaces with:
    - MAS API (static_mas_endpoint in config)
    - MICA bridge (provenance for constraint sets)
    - cuDF engine (GPU-accelerated constraint extraction from DB)
    - Unified search (constrained generative retrieval as search domain)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from .config import gpu_config

logger = logging.getLogger(__name__)


class ConstraintDomain(str, Enum):
    """Domains from which STATIC constraints can be extracted."""

    TAXONOMY = "taxonomy"           # core.taxon canonical names
    COMPOUNDS = "compounds"         # bio.compound names, InChIKeys
    DEVICES = "devices"             # telemetry device IDs
    OBSERVATIONS = "observations"   # observation source IDs
    EARTH_EVENTS = "earth_events"   # earthquake/wildfire/storm types
    SPECIES = "species"             # species.organism names (all kingdoms)
    MICA_OBJECTS = "mica_objects"    # mica.ca_object object_type values
    CUSTOM = "custom"               # user-defined constraint sets


@dataclass
class ConstraintSet:
    """A set of valid token sequences for STATIC constrained decoding."""

    domain: ConstraintDomain
    name: str
    sequences: List[str]
    version_hash: str = ""
    created_at: float = 0.0
    sequence_count: int = 0

    def __post_init__(self):
        self.sequence_count = len(self.sequences)
        if not self.version_hash:
            h = hashlib.sha256()
            for seq in sorted(self.sequences):
                h.update(seq.encode())
            self.version_hash = h.hexdigest()[:16]
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class ConstrainedDecodingRequest:
    """Request to MAS STATIC for constrained decoding."""

    prompt: str
    constraint_domain: ConstraintDomain
    constraint_set_hash: str
    max_tokens: int = 64
    temperature: float = 0.0
    top_k: int = 10
    include_scores: bool = True


@dataclass
class ConstrainedDecodingResult:
    """Result from MAS STATIC constrained decoding."""

    sequences: List[str]
    scores: List[float]
    constraint_domain: str
    constraint_set_hash: str
    latency_ms: float = 0.0
    model_id: str = ""


class STATICBridge:
    """Manages constraint extraction and MAS STATIC integration.

    Responsibilities:
        1. Extract valid sequences from MINDEX database tables
        2. Build constraint sets with version hashing
        3. Send constraint sets to MAS STATIC for index building
        4. Forward constrained decoding requests to MAS
        5. Cache constraint sets with TTL-based invalidation
    """

    def __init__(self) -> None:
        self._constraint_cache: Dict[str, ConstraintSet] = {}
        self._mas_endpoint = gpu_config.static_mas_endpoint
        self._max_constraints = gpu_config.static_max_constraints
        self._dense_layers = gpu_config.static_dense_layers

    @property
    def enabled(self) -> bool:
        return gpu_config.static_enabled and self._mas_endpoint is not None

    # ------------------------------------------------------------------
    # Constraint extraction from MINDEX tables
    # ------------------------------------------------------------------

    async def extract_constraints(
        self,
        domain: ConstraintDomain,
        db_session,
        limit: Optional[int] = None,
    ) -> ConstraintSet:
        """Extract valid token sequences from a MINDEX database domain.

        Queries the appropriate table and returns all valid identifiers
        that STATIC should allow during constrained decoding.
        """
        from sqlalchemy import text

        if limit is None:
            limit = self._max_constraints

        queries = {
            ConstraintDomain.TAXONOMY: """
                SELECT DISTINCT canonical_name FROM core.taxon
                WHERE canonical_name IS NOT NULL AND canonical_name != ''
                ORDER BY canonical_name LIMIT :limit
            """,
            ConstraintDomain.COMPOUNDS: """
                SELECT DISTINCT name FROM bio.compound
                WHERE name IS NOT NULL AND name != ''
                ORDER BY name LIMIT :limit
            """,
            ConstraintDomain.DEVICES: """
                SELECT DISTINCT device_id FROM telemetry.device
                WHERE device_id IS NOT NULL
                ORDER BY device_id LIMIT :limit
            """,
            ConstraintDomain.OBSERVATIONS: """
                SELECT DISTINCT source_id FROM obs.observation
                WHERE source_id IS NOT NULL
                ORDER BY source_id LIMIT :limit
            """,
            ConstraintDomain.EARTH_EVENTS: """
                SELECT DISTINCT event_type FROM (
                    SELECT 'earthquake' AS event_type
                    UNION SELECT 'volcano'
                    UNION SELECT 'wildfire'
                    UNION SELECT 'storm'
                    UNION SELECT 'lightning'
                    UNION SELECT 'tornado'
                    UNION SELECT 'flood'
                ) t ORDER BY event_type LIMIT :limit
            """,
            ConstraintDomain.SPECIES: """
                SELECT DISTINCT scientific_name FROM species.organism
                WHERE scientific_name IS NOT NULL AND scientific_name != ''
                ORDER BY scientific_name LIMIT :limit
            """,
            ConstraintDomain.MICA_OBJECTS: """
                SELECT DISTINCT object_type FROM mica.ca_object
                WHERE object_type IS NOT NULL
                ORDER BY object_type LIMIT :limit
            """,
        }

        query_str = queries.get(domain)
        if query_str is None:
            raise ValueError(f"No extraction query for domain: {domain}")

        try:
            result = await db_session.execute(text(query_str), {"limit": limit})
            rows = result.fetchall()
            sequences = [str(row[0]) for row in rows if row[0]]
        except Exception as e:
            logger.warning("Failed to extract constraints for %s: %s", domain, e)
            sequences = []

        constraint_set = ConstraintSet(
            domain=domain,
            name=f"mindex_{domain.value}",
            sequences=sequences,
        )

        # Cache it
        cache_key = f"{domain.value}"
        self._constraint_cache[cache_key] = constraint_set

        logger.info(
            "Extracted %d constraints for domain '%s' (hash: %s)",
            len(sequences), domain.value, constraint_set.version_hash,
        )
        return constraint_set

    # ------------------------------------------------------------------
    # MAS STATIC communication
    # ------------------------------------------------------------------

    async def push_constraints_to_mas(self, constraint_set: ConstraintSet) -> bool:
        """Send a constraint set to MAS for STATIC index building.

        MAS will:
            1. Tokenize all sequences
            2. Build trie from token sequences
            3. Flatten trie → CSR sparse matrix
            4. Create dense lookup tables for first d layers
            5. Store the STATIC index for inference-time masking
        """
        if not self.enabled:
            logger.debug("STATIC not enabled — skipping push to MAS")
            return False

        import httpx

        payload = {
            "constraint_set": {
                "domain": constraint_set.domain.value,
                "name": constraint_set.name,
                "sequences": constraint_set.sequences,
                "version_hash": constraint_set.version_hash,
                "sequence_count": constraint_set.sequence_count,
            },
            "index_params": {
                "dense_layers": self._dense_layers,
                "max_constraints": self._max_constraints,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._mas_endpoint}/static/index/build",
                    json=payload,
                )
                resp.raise_for_status()
                logger.info(
                    "Pushed %d constraints to MAS STATIC (domain=%s, hash=%s)",
                    constraint_set.sequence_count,
                    constraint_set.domain.value,
                    constraint_set.version_hash,
                )
                return True
        except Exception as e:
            logger.error("Failed to push constraints to MAS: %s", e)
            return False

    async def constrained_decode(
        self,
        request: ConstrainedDecodingRequest,
    ) -> Optional[ConstrainedDecodingResult]:
        """Send a constrained decoding request to MAS STATIC.

        MAS applies the STATIC mask during autoregressive decoding,
        ensuring the LLM only generates valid MINDEX entities.
        """
        if not self.enabled:
            logger.debug("STATIC not enabled — cannot perform constrained decoding")
            return None

        import httpx

        payload = {
            "prompt": request.prompt,
            "constraint_domain": request.constraint_domain.value,
            "constraint_set_hash": request.constraint_set_hash,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_k": request.top_k,
            "include_scores": request.include_scores,
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._mas_endpoint}/static/decode",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            elapsed_ms = (time.monotonic() - t0) * 1000
            return ConstrainedDecodingResult(
                sequences=data.get("sequences", []),
                scores=data.get("scores", []),
                constraint_domain=request.constraint_domain.value,
                constraint_set_hash=request.constraint_set_hash,
                latency_ms=elapsed_ms,
                model_id=data.get("model_id", ""),
            )
        except Exception as e:
            logger.error("Constrained decoding failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def get_cached_constraints(self, domain: ConstraintDomain) -> Optional[ConstraintSet]:
        """Get a cached constraint set for a domain."""
        return self._constraint_cache.get(domain.value)

    def list_cached_domains(self) -> Dict[str, Dict[str, Any]]:
        """List all cached constraint sets."""
        return {
            key: {
                "domain": cs.domain.value,
                "name": cs.name,
                "sequence_count": cs.sequence_count,
                "version_hash": cs.version_hash,
            }
            for key, cs in self._constraint_cache.items()
        }

    def clear_cache(self) -> None:
        """Clear all cached constraint sets."""
        self._constraint_cache.clear()


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_bridge: Optional[STATICBridge] = None


def get_static_bridge() -> STATICBridge:
    """Get the shared STATICBridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = STATICBridge()
    return _bridge
