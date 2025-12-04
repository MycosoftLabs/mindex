from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from psycopg import Connection


def normalize_name(name: str) -> str:
    if not name:
        raise ValueError("Taxon name cannot be empty")
    normalized = " ".join(name.strip().split())
    return normalized


def upsert_taxon(
    conn: Connection,
    *,
    canonical_name: str,
    rank: str,
    common_name: Optional[str] = None,
    authority: Optional[str] = None,
    description: Optional[str] = None,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> UUID:
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
            updates = {
                "common_name": common_name,
                "authority": authority,
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

        columns = ["canonical_name", "rank", "metadata"]
        values = [canonical, rank, json.dumps(metadata)]
        optional_fields = {
            "common_name": common_name,
            "authority": authority,
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
    metadata = metadata or {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM core.taxon_external_id
            WHERE taxon_id = %s AND source = %s AND external_id = %s
            """,
            (taxon_id, source, external_id),
        )
        if cur.fetchone():
            return
        cur.execute(
            """
            INSERT INTO core.taxon_external_id (taxon_id, source, external_id, metadata)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (taxon_id, source, external_id, json.dumps(metadata)),
        )
