"""Canonical civic unified models for MINDEX-first viewport intelligence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CivicProviderStatus(BaseModel):
    provider: str
    status: str
    fetched_at: datetime | None = None
    records: int = 0
    notes: str | None = None


class CivicRepresentative(BaseModel):
    id: str
    name: str
    office: str
    level: str | None = None
    party: str | None = None
    jurisdiction_name: str | None = None
    open_civic_division_id: str | None = None
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    address: str | None = None
    image_url: str | None = None
    provider_records: list[str] = Field(default_factory=list)


class CivicElectionEvent(BaseModel):
    id: str
    name: str
    election_day: str | None = None
    jurisdiction_name: str | None = None
    open_civic_division_id: str | None = None
    source_url: str | None = None
    provider_records: list[str] = Field(default_factory=list)


class CivicOffice(BaseModel):
    id: str
    name: str
    level: str | None = None
    jurisdiction_name: str | None = None
    open_civic_division_id: str | None = None
    provider_records: list[str] = Field(default_factory=list)


class CivicFacility(BaseModel):
    id: str
    name: str
    type: str
    lat: float
    lng: float
    agency: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    image_url: str | None = None
    source: str


class CivicViewportPlace(BaseModel):
    display_name: str | None = None
    country: str | None = None
    country_code: str | None = None
    state: str | None = None
    county: str | None = None
    city: str | None = None
    suburb: str | None = None
    postcode: str | None = None
    lat: float | None = None
    lng: float | None = None


class CivicViewportMeta(BaseModel):
    source_lineage: list[CivicProviderStatus] = Field(default_factory=list)
    dedupe_confidence: float = 1.0
    dedupe_strategy: str = "open-civic-data-ids+normalized-name-office"
    freshness_utc: str
    budget_ms: int = 1200
    total_ms: int = 0
    within_budget: bool = True
    provider_counts: dict[str, int] = Field(default_factory=dict)


class CivicUnifiedViewportResponse(BaseModel):
    ok: bool = True
    generated_at: str
    lod: str
    bounds: dict[str, float]
    center: dict[str, float]
    place: CivicViewportPlace | None = None
    representatives: list[CivicRepresentative] = Field(default_factory=list)
    offices: list[CivicOffice] = Field(default_factory=list)
    elections: list[CivicElectionEvent] = Field(default_factory=list)
    facilities: list[CivicFacility] = Field(default_factory=list)
    jurisdiction_stack: list[dict[str, Any]] = Field(default_factory=list)
    officials: list[dict[str, Any]] = Field(default_factory=list)
    legislation: list[dict[str, Any]] = Field(default_factory=list)
    finance_lobbying: list[dict[str, Any]] = Field(default_factory=list)
    budgets_debt_defense: list[dict[str, Any]] = Field(default_factory=list)
    media_gallery: list[dict[str, Any]] = Field(default_factory=list)
    meta: CivicViewportMeta


def open_civic_division_id(*, country_code: str | None, state: str | None, county: str | None, city: str | None) -> str | None:
    """Create a best-effort Open Civic Data division id."""
    if not country_code:
        return None
    cc = country_code.lower()
    segments: list[str] = [f"ocd-division/country:{cc}"]
    if state:
        segments.append(f"state:{state.strip().lower().replace(' ', '_')}")
    if county:
        segments.append(f"county:{county.strip().lower().replace(' ', '_')}")
    if city:
        segments.append(f"place:{city.strip().lower().replace(' ', '_')}")
    return "/".join(segments)


def dedupe_key_for_official(payload: dict[str, Any]) -> str:
    office = str(payload.get("office") or "").strip().lower()
    name = str(payload.get("name") or "").strip().lower()
    division = str(payload.get("open_civic_division_id") or "").strip().lower()
    return f"{division}|{office}|{name}"


def dedupe_key_for_office(payload: dict[str, Any]) -> str:
    office = str(payload.get("name") or "").strip().lower()
    division = str(payload.get("open_civic_division_id") or "").strip().lower()
    return f"{division}|{office}"

