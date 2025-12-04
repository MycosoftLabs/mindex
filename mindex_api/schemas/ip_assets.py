from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Base64Bytes, BaseModel, Field

from .common import PaginationMeta, TimestampedModel


class HypergraphAnchor(BaseModel):
    id: UUID
    anchor_hash: str
    sample_id: Optional[UUID] = None
    anchored_at: datetime
    metadata: dict = Field(default_factory=dict)


class BitcoinOrdinal(BaseModel):
    id: UUID
    content_hash: str
    inscription_id: str
    inscription_address: Optional[str] = None
    inscribed_at: datetime
    metadata: dict = Field(default_factory=dict)


class SolanaBinding(BaseModel):
    id: UUID
    mint_address: str
    token_account: Optional[str] = None
    bound_at: datetime
    metadata: dict = Field(default_factory=dict)


class IPAsset(TimestampedModel):
    id: UUID
    name: str
    description: Optional[str] = None
    taxon_id: Optional[UUID] = None
    created_by: Optional[str] = None
    content_hash: Optional[str] = None
    content_uri: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    hypergraph_anchors: List[HypergraphAnchor] = Field(default_factory=list)
    bitcoin_ordinals: List[BitcoinOrdinal] = Field(default_factory=list)
    solana_bindings: List[SolanaBinding] = Field(default_factory=list)


class IPAssetListResponse(BaseModel):
    data: List[IPAsset]
    pagination: PaginationMeta


class HypergraphAnchorRequest(BaseModel):
    payload_b64: Base64Bytes = Field(..., description="Payload to hash and anchor.")
    metadata: dict = Field(default_factory=dict)
    sample_id: Optional[UUID] = Field(
        None,
        description="Optional telemetry sample to link.",
    )


class OrdinalAnchorRequest(BaseModel):
    payload_b64: Base64Bytes = Field(..., description="Payload representing the ordinal content.")
    inscription_id: str = Field(..., description="External Ordinal inscription identifier.")
    inscription_address: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class SolanaBindingRequest(BaseModel):
    mint_address: str
    token_account: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
