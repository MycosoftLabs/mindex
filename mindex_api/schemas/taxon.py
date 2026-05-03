from __future__ import annotations

from typing import List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field

from .common import PaginationMeta, TimestampedModel


class TaxonTrait(BaseModel):
    id: Union[int, UUID]
    trait_name: str
    value_text: Optional[str] = None
    value_numeric: Optional[float] = None
    value_unit: Optional[str] = None
    source: Optional[str] = None


class TaxonBase(TimestampedModel):
    id: Union[int, UUID]
    canonical_name: str
    rank: str
    common_name: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
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


class TaxonResponse(TaxonBase):
    traits: List[TaxonTrait] = Field(default_factory=list)


class TaxonListResponse(BaseModel):
    data: List[TaxonBase]
    pagination: PaginationMeta
