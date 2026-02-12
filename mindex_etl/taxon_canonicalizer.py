from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from psycopg import Connection


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
) -> int:
    """
    Upsert a taxon record.
    
    Note: The 'authority' parameter maps to the 'author' column in the database.
    """
    metadata = metadata or {}
    canonical = normalize_name(canonical_name)
    
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

        # Insert new record - scientific_name is required, canonical_name is optional
        columns = ["scientific_name", "canonical_name", "rank", "metadata"]
        values = [canonical, canonical, rank, json.dumps(metadata)]
        optional_fields = {
            "common_name": common_name,
            "author": authority,  # DB column is 'author', not 'authority'
            "description": description,
            "source": source,
        }
        for col, value in optional_fields.items():
            if value is not None:
                columns.append(col)
                values.append(value)

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
