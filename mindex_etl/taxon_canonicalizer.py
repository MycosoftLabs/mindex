from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from psycopg import Connection

ALLOWED_KINGDOMS = frozenset(
    {
        "Fungi",
        "Plantae",
        "Animalia",
        "Bacteria",
        "Archaea",
        "Protista",
        "Viruses",
        "Undesignated",
    }
)

# iNaturalist iconic_taxon_name and common aliases → core.taxon.kingdom check values
_KINGDOM_ALIASES: dict[str, str] = {
    "fungi": "Fungi",
    "plantae": "Plantae",
    "plants": "Plantae",
    "animalia": "Animalia",
    "mammalia": "Animalia",
    "aves": "Animalia",
    "reptilia": "Animalia",
    "amphibia": "Animalia",
    "actinopterygii": "Animalia",
    "insecta": "Animalia",
    "arachnida": "Animalia",
    "mollusca": "Animalia",
    "chromista": "Protista",
    "protozoa": "Protista",
    "bacteria": "Bacteria",
    "archaea": "Archaea",
    "viruses": "Viruses",
    "viridae": "Viruses",
}


def normalize_kingdom(
    value: Optional[str],
    *,
    default: str = "Undesignated",
    source: Optional[str] = None,
) -> str:
    """Map external kingdom / iconic taxon strings to core.taxon CHECK constraint values."""
    if source in ("mycobank", "theyeasts", "fusarium", "mushroom_world"):
        return "Fungi"
    if not value or not str(value).strip():
        return default
    raw = str(value).strip()
    if raw in ALLOWED_KINGDOMS:
        return raw
    low = raw.lower().replace("_", " ")
    if low in _KINGDOM_ALIASES:
        return _KINGDOM_ALIASES[low]
    for key, kingdom in _KINGDOM_ALIASES.items():
        if key in low:
            return kingdom
    return default


def normalize_name(name: str) -> str:
    if not name or not name.strip():
        raise ValueError("Taxon name cannot be empty")
    normalized = " ".join(name.strip().split())
    return normalized


def upsert_taxon(
    conn: Connection,
    *,
    canonical_name: str,
    rank: str,
    common_name: Optional[str] = None,
    authority: Optional[str] = None,  # Maps to 'author' column in DB
    description: Optional[str] = None,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    kingdom: Optional[str] = None,
) -> int:
    """
    Upsert a taxon record.
    
    Note: The 'authority' parameter maps to the 'author' column in the database.
    """
    metadata = metadata or {}
    canonical = normalize_name(canonical_name)
    if kingdom is not None:
        kingdom = normalize_kingdom(kingdom, source=source)
    elif source in ("mycobank", "theyeasts", "fusarium", "mushroom_world"):
        kingdom = "Fungi"

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM core.taxon
            WHERE canonical_name = %s AND rank = %s
            LIMIT 1
            """,
            (canonical, rank),
        )
        row = cur.fetchone()
        if row:
            taxon_id = row["id"]
            # Update existing record
            updates = {
                "common_name": common_name,
                "author": authority,  # DB column is 'author', not 'authority'
                "description": description,
                "source": source,
                "metadata": json.dumps(metadata) if metadata else None,
                "kingdom": kingdom,
            }
            set_parts = [f"{col} = %s" for col, value in updates.items() if value is not None]
            params = [value for value in updates.values() if value is not None]
            if set_parts:
                set_clause = ", ".join(set_parts + ["updated_at = now()"])
                cur.execute(
                    f"UPDATE core.taxon SET {set_clause} WHERE id = %s",
                    (*params, taxon_id),
                )
            return taxon_id

        # Insert: prefer canonical_name (core.taxon); scientific_name if column exists
        columns = ["canonical_name", "rank", "metadata"]
        values: list = [canonical, rank, json.dumps(metadata)]
        optional_fields = {
            "common_name": common_name,
            "author": authority,  # DB column is 'author', not 'authority'
            "description": description,
            "source": source,
            "kingdom": kingdom,
        }
        for col, value in optional_fields.items():
            if value is not None:
                columns.append(col)
                values.append(value)
        if "scientific_name" not in columns:
            try:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='core' AND table_name='taxon' AND column_name='scientific_name'"
                )
                if cur.fetchone():
                    columns = ["scientific_name"] + [c for c in columns if c != "scientific_name"]
                    values = [canonical] + values
            except Exception:
                pass

        placeholders = ", ".join(["%s"] * len(values))
        cur.execute(
            f"""
            INSERT INTO core.taxon ({', '.join(columns)})
            VALUES ({placeholders})
            RETURNING id
            """,
            values,
        )
        return cur.fetchone()["id"]


def link_external_id(
    conn: Connection,
    *,
    taxon_id: UUID,
    source: str,
    external_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Link an external ID to a taxon.
    
    Uses ON CONFLICT to handle duplicate (source, external_id) entries.
    If a link already exists for this (source, external_id), it updates the taxon_id
    to the new value (last writer wins).
    """
    metadata = metadata or {}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.taxon_external_id (taxon_id, source, external_id, metadata)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (source, external_id) DO UPDATE
            SET taxon_id = EXCLUDED.taxon_id,
                metadata = EXCLUDED.metadata
            """,
            (taxon_id, source, external_id, json.dumps(metadata)),
        )
