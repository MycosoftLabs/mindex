"""
MINDEX Species Validator
========================
Deduplication and validation for species/taxon data:
- UUID-based deduplication for species
- Image hash-based deduplication (perceptual hash for near-duplicates)
- Reference ID validation (iNaturalist, GBIF, GenBank IDs)
- Quality scoring for data completeness

Used by ETL pipelines and auto_enrich_species job.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID


# Valid ID patterns by source
INAT_ID_PATTERN = re.compile(r"^[0-9]+$")  # Numeric taxon ID
GBIF_SPECIES_KEY_PATTERN = re.compile(r"^[0-9]+$")  # Numeric species key
GENBANK_ACCESSION_PATTERN = re.compile(
    r"^[A-Z]{1,2}[0-9]{5,8}(?:[\.][0-9]+)?$|^[A-Z]{2}_[0-9]{6,}\.[0-9]+$"
)  # e.g. AF123456, NC_001144.1


@dataclass
class ValidationResult:
    """Result of species validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


def validate_reference_id(source: str, external_id: Optional[str]) -> bool:
    """
    Validate reference ID format for common external sources.
    
    Args:
        source: Source name (inat, gbif, genbank, ncbi, mycobank)
        external_id: The external ID string
    
    Returns:
        True if valid format
    """
    if not external_id or not isinstance(external_id, str):
        return False
    
    ext_id = external_id.strip()
    if not ext_id:
        return False
    
    source_lower = source.lower()
    
    if source_lower in ("inat", "inaturalist"):
        return bool(INAT_ID_PATTERN.match(ext_id))
    if source_lower == "gbif":
        return bool(GBIF_SPECIES_KEY_PATTERN.match(ext_id))
    if source_lower in ("genbank", "ncbi", "bold"):
        return bool(GENBANK_ACCESSION_PATTERN.match(ext_id.upper()))
    if source_lower == "mycobank":
        # MycoBank IDs are alphanumeric (e.g. MB123456)
        return bool(re.match(r"^MB[0-9]+$", ext_id, re.I))
    
    # Unknown source: accept non-empty alphanumeric
    return bool(re.match(r"^[A-Za-z0-9_.-]+$", ext_id))


def validate_image_hash(
    image_url: str,
    known_hashes: Set[str],
    *,
    content_hash: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Check if image is duplicate by URL hash or content hash.
    
    Uses URL hash for quick dedup without fetching. Optionally accepts
    precomputed content hash (e.g. from downloaded bytes).
    
    Args:
        image_url: Image URL
        known_hashes: Set of already-seen hashes
        content_hash: Optional precomputed hash of image bytes
    
    Returns:
        (is_duplicate, hash_used)
    """
    if content_hash:
        h = content_hash
    else:
        h = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
    
    if h in known_hashes:
        return True, h
    return False, h


def compute_quality_score(
    *,
    has_image: bool = False,
    has_description: bool = False,
    has_genetics: bool = False,
    has_chemistry: bool = False,
    image_count: int = 0,
    description_length: int = 0,
) -> float:
    """
    Compute data completeness quality score (0.0 to 1.0).
    
    Weights:
        - Image: 0.30 (primary visual)
        - Description: 0.25
        - Genetics: 0.25
        - Chemistry: 0.20
        - Bonus for multiple images (up to 8)
    """
    score = 0.0
    
    if has_image:
        score += 0.30
    if has_description:
        score += 0.25
        # Slight bonus for longer descriptions
        if description_length > 200:
            score += 0.02
        elif description_length > 100:
            score += 0.01
    if has_genetics:
        score += 0.25
    if has_chemistry:
        score += 0.20
    
    # Image count bonus (up to +0.05 for 8 images)
    if image_count > 1:
        score += min(0.05, (image_count - 1) * 0.007)
    
    return min(1.0, round(score, 3))


def validate_species_record(
    record: Dict[str, Any],
    known_uuid: Optional[UUID] = None,
    known_url_hashes: Optional[Set[str]] = None,
) -> ValidationResult:
    """
    Full validation of a species/taxon record.
    
    Checks:
        - UUID uniqueness (if known_uuid provided)
        - Reference IDs validity (inat_id, gbif_key, etc.)
        - Image URL hash deduplication
        - Quality score
    """
    errors: List[str] = []
    warnings: List[str] = []
    known_hashes = known_url_hashes or set()
    
    # Reference IDs
    metadata = record.get("metadata") or {}
    if metadata.get("inat_id") and not validate_reference_id("inat", str(metadata["inat_id"])):
        warnings.append("Invalid iNaturalist ID format")
    if metadata.get("gbif_key") and not validate_reference_id("gbif", str(metadata["gbif_key"])):
        warnings.append("Invalid GBIF key format")
    
    # Image dedup
    default_photo = metadata.get("default_photo")
    if isinstance(default_photo, dict):
        url = default_photo.get("url") or default_photo.get("medium_url") or default_photo.get("original_url")
        if url:
            is_dup, h = validate_image_hash(url, known_hashes)
            if is_dup:
                warnings.append("Duplicate image (hash match)")
    
    # Quality score
    has_image = bool(default_photo and (default_photo.get("url") or default_photo.get("medium_url")))
    has_desc = bool(record.get("description") and str(record.get("description")).strip())
    desc_len = len(record.get("description") or "")
    qscore = compute_quality_score(
        has_image=has_image,
        has_description=has_desc,
        has_genetics=record.get("has_genetics", False),
        has_chemistry=record.get("has_chemistry", False),
        image_count=1 if has_image else 0,
        description_length=desc_len,
    )
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        quality_score=qscore,
        metadata={"validated_fields": ["reference_ids", "image_hash", "quality"]},
    )
