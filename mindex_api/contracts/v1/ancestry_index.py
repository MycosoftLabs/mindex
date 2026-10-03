"""Read-only collection index response used by Ancestry."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


IdentityState = Literal["linked", "unresolved", "ambiguous"]


class FungiPLaunchAssociation(BaseModel):
    """A source-reported launch association, separate from chain receipts."""

    model_config = ConfigDict(extra="forbid")

    species_id: str
    snapshot_sha256: str
    payload_sha256: str
    catalog_sha256: str
    launch_sha256: str
    handoff_sha256: str
    correction_sha256: str
    launch_schema: str
    superseded_encoding: str
    corrected_input_version: str
    authority_approval_reference: str
    source_reported_as_of_utc: datetime
    source_reported_as_of_pt: str
    correction_recorded_at_utc: datetime
    ticker: str
    accepted_name: str
    dna_sha256: str
    dna_accession_version: str
    dna_database: str
    dna_source_url: str
    image_credit: str
    image_license: str
    image_sha256: str
    launch_status: str
    source_hash_match: bool
    canonical_approved: bool
    owner_entity: str
    mint_address: Optional[str] = None
    launch_tx: Optional[str] = None
    launched_at: Optional[datetime] = None
    launched_at_pt: Optional[str] = None
    solana_explorer_url: Optional[str] = None
    solana_explorer_tx_url: Optional[str] = None
    solscan_tx_url: Optional[str] = None
    solscan_token_url: Optional[str] = None
    usepaid_url: Optional[str] = None
    usepaid_short_url: Optional[str] = None
    pumpfun_url: Optional[str] = None
    metadata_uri: Optional[str] = None
    recipient: Optional[str] = None
    token_program: Optional[str] = None
    decimals: Optional[int] = None
    supply_raw: Optional[str] = None
    candidate_launch: Optional[dict[str, Any]] = None
    superseded_mints: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    catalog_review_flags: list[str] = Field(default_factory=list)
    gap_flags: list[str] = Field(default_factory=list)
    verification_basis: str
    new_chain_verified: Literal[False] = False


class FungiPIndexMember(BaseModel):
    """Stable source identity and optional, exact MINDEX taxon linkage."""

    model_config = ConfigDict(extra="forbid")

    species_id: str
    mindex_uuid: Optional[UUID] = None
    canonical_taxon_uuid: Optional[UUID] = None
    candidate_taxon_uuids: list[UUID] = Field(default_factory=list)
    accepted_name: str
    requested_name: Optional[str] = None
    common_name: Optional[str] = None
    fungal_group: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    catalog_review_flags: list[str] = Field(default_factory=list)
    ticker: Optional[str] = None
    accession_version: Optional[str] = None
    reference_dna_available: bool = False
    complete_its_available: bool = False
    token_confirmed: bool = False
    feature_label: str
    missing_data_flags: list[str] = Field(default_factory=list)
    image: Optional[Any] = None
    resolution_status: str
    identity_state: IdentityState
    identity_reason: str
    canonical_url: Optional[str] = None
    record_sha256: str
    catalog_sha256: str
    validation_errors: list[Any] = Field(default_factory=list)
    source_identifiers: list[dict[str, Any]] = Field(default_factory=list)
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    first40_launch: Optional[FungiPLaunchAssociation] = None
    chain_receipt_status: Literal["confirmed", "not_confirmed"]
    launch_association_state: Literal[
        "source_verified", "invalid_binding", "not_associated", "unavailable", "not_included",
    ] = "not_included"


class FungiPTaxonIndexRow(BaseModel):
    """Taxa-list-shaped row; id is null until a canonical taxon is verified."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[UUID] = None
    canonical_name: Optional[str] = None
    common_name: Optional[str] = None
    rank: Literal["species"] = "species"
    kingdom: Literal["Fungi"] = "Fungi"
    obs_count: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    fungip: FungiPIndexMember


class FungiPIndexPagination(BaseModel):
    limit: int
    offset: int
    total: int


class FungiPIndexCounts(BaseModel):
    listed: int
    linked: int
    unresolved: int
    ambiguous: int
    launch_associations: Optional[int] = None
    superseded_exclusions: Optional[int] = None


class FungiPLaunchIndexState(BaseModel):
    state: Literal["available", "unavailable"]
    verification_basis: Optional[str] = None
    new_chain_verified: Literal[False] = False


class FungiPIndexAvailability(BaseModel):
    status: Literal["available", "unavailable", "error"]
    reason: Optional[Literal["source_table_missing", "query_failed"]] = None


class FungiPTaxonIndexResponse(BaseModel):
    collection: Literal["FungiP 300"] = "FungiP 300"
    data: list[FungiPTaxonIndexRow]
    pagination: FungiPIndexPagination
    counts: FungiPIndexCounts
    launch_index: FungiPLaunchIndexState
