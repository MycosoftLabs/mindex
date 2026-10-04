from __future__ import annotations

from typing import List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field

from ..contracts.v1.ancestry_index import FungiPIndexAvailability, FungiPIndexMember
from .common import PaginationMeta, TimestampedModel


class TaxonTrait(BaseModel):
    id: Union[int, UUID]
    trait_name: str
    value_text: Optional[str] = None
    value_numeric: Optional[float] = None
    value_unit: Optional[str] = None
    source: Optional[str] = None


class TaxonFamilyEvidence(BaseModel):
    source: Literal["core.taxon.metadata.family", "fungip.species.record.taxonomy.family"]
    value: str = Field(min_length=1, max_length=200)
    species_id: Optional[str] = Field(default=None, max_length=64)


class TaxonCategoryEvidence(BaseModel):
    category: Literal["edible", "medicinal", "poisonous", "psychoactive", "gourmet"]
    source: Literal[
        "core.taxon.metadata.edibility",
        "core.taxon.metadata.characteristics",
        "bio.taxon_trait",
        "bio.taxon_characteristic",
    ]
    value: str = Field(min_length=1, max_length=120)


class TaxonImageSelection(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    source: Literal[
        "fungip.species.record.image.image_url",
        "fungip.species.record.image.url",
        "core.taxon.metadata.default_photo.medium_url",
        "core.taxon.metadata.default_photo.url",
        "core.taxon.metadata.photos[0].url",
    ]
    attribution: Optional[str] = Field(default=None, max_length=512)
    license_code: Optional[str] = Field(default=None, max_length=128)
    source_url: Optional[str] = Field(default=None, max_length=2048)


class TaxonBase(TimestampedModel):
    id: Union[int, UUID]
    canonical_name: str
    rank: str
    common_name: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    family: Optional[str] = Field(default=None, max_length=200)
    family_source: Optional[Literal["core.taxon.metadata.family", "fungip.species.record.taxonomy.family", "unknown"]] = None
    family_evidence: List[TaxonFamilyEvidence] = Field(default_factory=list, max_length=2)
    category_evidence: List[TaxonCategoryEvidence] = Field(default_factory=list, max_length=64)
    category_evidence_truncated: bool = False
    image_selection: Optional[TaxonImageSelection] = None
    # All-life / universal taxonomy (from migration 20260502, bio.taxon_full)
    kingdom: Optional[str] = None
    lineage: Optional[List[str]] = None
    lineage_ids: Optional[List[UUID]] = None
    external_ids: dict = Field(default_factory=dict)
    # Aggregates from bio.taxon_full (list/detail when selected from view)
    obs_count: Optional[int] = None
    image_count: Optional[int] = None
    video_count: Optional[int] = None
    audio_count: Optional[int] = None
    genome_count: Optional[int] = None
    compound_link_count: Optional[int] = None
    interaction_count: Optional[int] = None
    publication_count: Optional[int] = None
    characteristic_count: Optional[int] = None
    fungip: Optional[FungiPIndexMember] = None


class TaxonResponse(TaxonBase):
    traits: List[TaxonTrait] = Field(default_factory=list)
    fungip_index: Optional[FungiPIndexAvailability] = None


class TaxonListQueryMeta(BaseModel):
    """Truthful scope for native filtered taxon pages and matching totals."""

    contract_version: Literal["mycosoft.mindex.ancestry.filtered-catalog.v2"]
    status: Literal["available", "empty", "partial", "unavailable"]
    count_scope: Literal["matching_core_taxa"]
    count_consistency: Literal["best_effort_not_atomic"]
    count_cache_state: Literal["cache_hit", "fresh_query"]
    count_cache_ttl_seconds: int = Field(ge=0, le=3600)
    filter_sources: dict[
        Literal["family", "category", "has_images", "observation_tiebreak_photo", "has_description"],
        str,
    ] = Field(default_factory=dict, max_length=5)
    partial_reasons: list[str] = Field(default_factory=list, max_length=16)


class TaxonListResponse(BaseModel):
    data: List[TaxonBase]
    pagination: PaginationMeta
    fungip_index: Optional[FungiPIndexAvailability] = None
    query: Optional[TaxonListQueryMeta] = None
