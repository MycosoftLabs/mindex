"""
Compound API Schemas

Pydantic models for compound-related API endpoints.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import PaginationMeta, TimestampedModel


# =============================================================================
# COMPOUND SCHEMAS
# =============================================================================

class CompoundBase(BaseModel):
    """Base compound model with core fields."""
    name: str
    iupac_name: Optional[str] = None
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    smiles: Optional[str] = None
    inchi: Optional[str] = None
    inchikey: Optional[str] = None
    chemical_class: Optional[str] = None
    compound_type: Optional[str] = None


class CompoundCreate(CompoundBase):
    """Schema for creating a compound."""
    chemspider_id: Optional[int] = None
    pubchem_id: Optional[int] = None
    cas_number: Optional[str] = None
    chebi_id: Optional[str] = None
    source: str = "manual"
    metadata: dict = Field(default_factory=dict)


class CompoundUpdate(BaseModel):
    """Schema for updating a compound (all fields optional)."""
    name: Optional[str] = None
    iupac_name: Optional[str] = None
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    smiles: Optional[str] = None
    inchi: Optional[str] = None
    inchikey: Optional[str] = None
    chemspider_id: Optional[int] = None
    pubchem_id: Optional[int] = None
    cas_number: Optional[str] = None
    chebi_id: Optional[str] = None
    chemical_class: Optional[str] = None
    compound_type: Optional[str] = None
    metadata: Optional[dict] = None


class BiologicalActivity(BaseModel):
    """Biological activity linked to a compound."""
    activity_id: UUID
    activity_name: str
    category: Optional[str] = None
    potency: Optional[str] = None
    evidence_level: Optional[str] = None


class CompoundResponse(TimestampedModel):
    """Full compound response with all fields."""
    id: UUID
    name: str
    iupac_name: Optional[str] = None
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    monoisotopic_mass: Optional[float] = None
    smiles: Optional[str] = None
    inchi: Optional[str] = None
    inchikey: Optional[str] = None
    chemspider_id: Optional[int] = None
    pubchem_id: Optional[int] = None
    cas_number: Optional[str] = None
    chebi_id: Optional[str] = None
    chemical_class: Optional[str] = None
    compound_type: Optional[str] = None
    source: str
    metadata: dict = Field(default_factory=dict)
    
    # Related data
    activities: List[BiologicalActivity] = Field(default_factory=list)
    species_count: int = 0


class CompoundListResponse(BaseModel):
    """Paginated list of compounds."""
    data: List[CompoundResponse]
    limit: int
    offset: int
    total: Optional[int] = None


class CompoundSearchRequest(BaseModel):
    """Search request for compounds."""
    query: Optional[str] = None
    formula: Optional[str] = None
    smiles: Optional[str] = None
    inchikey: Optional[str] = None
    chemical_class: Optional[str] = None
    compound_type: Optional[str] = None
    min_molecular_weight: Optional[float] = None
    max_molecular_weight: Optional[float] = None
    has_activity: Optional[str] = None  # Activity name filter


# =============================================================================
# TAXON-COMPOUND RELATIONSHIP SCHEMAS
# =============================================================================

class TaxonCompoundCreate(BaseModel):
    """Create a taxon-compound relationship."""
    taxon_id: UUID
    compound_id: UUID
    relationship_type: str = "produces"
    evidence_level: str = "reported"
    concentration_min: Optional[float] = None
    concentration_max: Optional[float] = None
    concentration_unit: Optional[str] = None
    tissue_location: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    doi: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class TaxonCompoundResponse(TimestampedModel):
    """Taxon-compound relationship response."""
    id: UUID
    taxon_id: UUID
    compound_id: UUID
    relationship_type: str
    evidence_level: str
    concentration_min: Optional[float] = None
    concentration_max: Optional[float] = None
    concentration_unit: Optional[str] = None
    tissue_location: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    doi: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    
    # Compound info (when joined)
    compound_name: Optional[str] = None
    compound_formula: Optional[str] = None


class CompoundForTaxonResponse(BaseModel):
    """Compound data when fetched for a specific taxon."""
    compound_id: UUID
    name: str
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    chemspider_id: Optional[int] = None
    relationship_type: str
    evidence_level: str
    tissue_location: Optional[str] = None


class TaxonCompoundsResponse(BaseModel):
    """Response for taxon compounds endpoint."""
    taxon_id: UUID
    canonical_name: str
    common_name: Optional[str] = None
    compounds: List[CompoundForTaxonResponse] = Field(default_factory=list)


# =============================================================================
# COMPOUND PROPERTY SCHEMAS
# =============================================================================

class CompoundPropertyCreate(BaseModel):
    """Create a compound property."""
    property_name: str
    property_category: Optional[str] = None
    value_text: Optional[str] = None
    value_numeric: Optional[float] = None
    value_boolean: Optional[bool] = None
    value_unit: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    doi: Optional[str] = None


class CompoundPropertyResponse(BaseModel):
    """Compound property response."""
    id: UUID
    compound_id: UUID
    property_name: str
    property_category: Optional[str] = None
    value_text: Optional[str] = None
    value_numeric: Optional[float] = None
    value_boolean: Optional[bool] = None
    value_unit: Optional[str] = None
    source: Optional[str] = None


# =============================================================================
# CHEMSPIDER ENRICHMENT SCHEMAS
# =============================================================================

class ChemSpiderEnrichRequest(BaseModel):
    """Request to enrich a compound from ChemSpider."""
    compound_id: Optional[UUID] = None
    compound_name: Optional[str] = None
    chemspider_id: Optional[int] = None
    smiles: Optional[str] = None
    inchikey: Optional[str] = None


class ChemSpiderEnrichResponse(BaseModel):
    """Response from ChemSpider enrichment."""
    success: bool
    compound_id: Optional[UUID] = None
    chemspider_id: Optional[int] = None
    enriched_fields: List[str] = Field(default_factory=list)
    message: str


class ChemSpiderSearchRequest(BaseModel):
    """Search ChemSpider directly."""
    query: str
    search_type: str = "name"  # name, formula, smiles, inchi
    max_results: int = 10


class ChemSpiderSearchResult(BaseModel):
    """A single ChemSpider search result."""
    chemspider_id: int
    name: Optional[str] = None
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    smiles: Optional[str] = None
    inchikey: Optional[str] = None


class ChemSpiderSearchResponse(BaseModel):
    """ChemSpider search results."""
    query: str
    search_type: str
    results: List[ChemSpiderSearchResult] = Field(default_factory=list)
    total_count: int = 0


# =============================================================================
# BIOLOGICAL ACTIVITY SCHEMAS
# =============================================================================

class BiologicalActivityCreate(BaseModel):
    """Create a biological activity."""
    name: str
    category: Optional[str] = None
    description: Optional[str] = None


class BiologicalActivityResponse(BaseModel):
    """Biological activity response."""
    id: UUID
    name: str
    category: Optional[str] = None
    description: Optional[str] = None


class CompoundActivityCreate(BaseModel):
    """Link a compound to a biological activity."""
    compound_id: UUID
    activity_id: UUID
    potency: Optional[str] = None
    mechanism: Optional[str] = None
    target: Optional[str] = None
    evidence_level: str = "reported"
    source: Optional[str] = None
    doi: Optional[str] = None
