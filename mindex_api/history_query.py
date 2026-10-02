"""Canonical event-time reads. No acquisition, background work or ledger writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

HISTORY_TABLES = {"observations": "obs.observation", "crep_entities": "crep.unified_entities"}


@dataclass(frozen=True)
class HistoryWindow:
    since: Optional[datetime]
    until: Optional[datetime]

    def filters(self) -> dict:
        return {name: value.isoformat() for name, value in
                (("since", self.since), ("until", self.until)) if value is not None}


def parse_history_window(since: Optional[str], until: Optional[str]) -> Optional[HistoryWindow]:
    """Accept explicit UTC offsets only; lower inclusive, upper exclusive."""
    if since is None and until is None:
        return None

    def parse(value: Optional[str], name: str) -> Optional[datetime]:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timezone required")
            return parsed.astimezone(timezone.utc)
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise HTTPException(422, detail={"code": "invalid_history_time", "field": name,
                "message": "Use an ISO datetime with explicit UTC offset; date-only values are ambiguous."}) from exc

    window = HistoryWindow(parse(since, "since"), parse(until, "until"))
    if window.since is not None and window.until is not None and window.since >= window.until:
        raise HTTPException(422, detail={"code": "invalid_history_range",
            "message": "since must be earlier than until; interval is [since, until)."})
    return window


def require_history_domains(domains: list[str]) -> None:
    unsupported = sorted(set(domains) - HISTORY_TABLES.keys())
    if unsupported:
        raise HTTPException(422, detail={"code": "history_domain_unsupported",
            "unsupported_domains": unsupported, "supported_domains": list(HISTORY_TABLES),
            "message": "No qualified event-time query for these domains; current rows are not historical snapshots."})


def history_sql(domain: str, query: str, limit: int, window: HistoryWindow,
                lat: Optional[float], lng: Optional[float], radius: Optional[float]) -> tuple[str, dict]:
    """Fixed table/column allowlist and bound values; predicates precede ORDER/LIMIT."""
    require_history_domains([domain])
    params = {"query": f"%{query}%", "limit": limit}
    timestamp = "o.observed_at" if domain == "observations" else "e.observed_at"
    where = []
    for name, operator in (("since", ">="), ("until", "<")):
        value = getattr(window, name)
        if value is not None:
            where.append(f"{timestamp} {operator} :{name}")
            params[name] = value
    if (lat is None) != (lng is None):
        raise HTTPException(422, detail={"code": "incomplete_location", "message": "Provide both lat and lng."})
    if lat is not None and lng is not None:
        import math
        if not (math.isfinite(lat) and -90 <= lat <= 90 and math.isfinite(lng) and -180 <= lng <= 180):
            raise HTTPException(422, detail={"code": "invalid_location"})
        if radius is not None and (not math.isfinite(radius) or radius <= 0):
            raise HTTPException(422, detail={"code": "invalid_radius"})
        column = "o.location" if domain == "observations" else "e.geometry"
        where.append(f"ST_DWithin({column}::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius_m)")
        params.update(lat=lat, lng=lng, radius_m=(radius if radius is not None else 100) * 1000)
    if domain == "observations":
        where.append("(t.canonical_name ILIKE :query OR t.common_name ILIKE :query)")
        select = """SELECT o.id::text AS id, o.taxon_id::text AS taxon_id,
            t.canonical_name AS taxon_name, t.common_name,
            ST_Y(o.location::geometry) AS lat, ST_X(o.location::geometry) AS lng,
            o.observed_at::text AS observed_at, o.source, o.source_id,
            'obs.observation' AS record_table
            FROM obs.observation o LEFT JOIN core.taxon t ON t.id = o.taxon_id"""
    else:
        where.append("(e.entity_type ILIKE :query OR e.state->>'name' ILIKE :query)")
        select = """SELECT e.id, e.entity_type, e.state, e.source, e.confidence,
            e.observed_at::text AS occurred_at,
            ST_Y(e.geometry::geometry) AS lat, ST_X(e.geometry::geometry) AS lng,
            'crep.unified_entities' AS record_table FROM crep.unified_entities e"""
    return f"{select} WHERE {' AND '.join(where)} ORDER BY {timestamp} DESC, {'o' if domain == 'observations' else 'e'}.id LIMIT :limit", params


def coverage(domains: list[str], results: dict, window: Optional[HistoryWindow], *, cached: bool = False) -> dict:
    return {
        "read_policy": "internal_only", "external_fallback": False,
        "mode": "retained_event_time" if window else "retained_current_records",
        "interval": "[since, until)" if window else None,
        "supported_history_domains": list(HISTORY_TABLES),
        "queried_tables": {d: HISTORY_TABLES[d] for d in domains} if window else {},
        "empty_domains": [d for d in domains if not results.get(d)],
        "completeness": "unverified", "ingestion_watermark": None,
        "cached": cached, "cache_max_age_seconds": 120 if cached else 0,
        "note": "Empty means no retained matches, not no real-world events. Observation names are current catalog labels; event-time filtering is not an as-of catalog reconstruction. Species sightings are not included in the qualified observations history slice.",
    }
