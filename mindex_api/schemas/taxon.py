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


class TaxonResponse(TaxonBase):
    traits: List[TaxonTrait] = Field(default_factory=list)


class TaxonListResponse(BaseModel):
    data: List[TaxonBase]
    pagination: PaginationMeta
