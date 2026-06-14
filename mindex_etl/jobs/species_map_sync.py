"""
Sync iNaturalist (and other) observations into species.organisms + species.sightings
for Earth map bbox layers. Primary ingest remains obs.observation; this mirrors rows
for PostGIS map queries that use the species.* schema.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from psycopg import Connection

from ..taxon_canonicalizer import normalize_kingdom


def _organism_source_id(obs: Dict[str, Any]) -> str:
    taxon_inat_id = obs.get("taxon_inat_id")
    if taxon_inat_id is not None:
        return str(taxon_inat_id)
    name = obs.get("taxon_name") or "unknown"
    return f"name:{name}"


def _first_photo_url(obs: Dict[str, Any]) -> Optional[str]:
    photos = obs.get("photos") or []
    if not photos:
        return None
    first = photos[0]
    if isinstance(first, dict):
        return first.get("url")
    return None


def upsert_species_map_rows(
    conn: Connection,
    obs: Dict[str, Any],
    *,
    core_taxon_id: Optional[UUID | str] = None,
) -> None:
    """
    Upsert species.organisms + species.sightings for one mapped iNat observation.
    No-op when coordinates are missing (sightings.location is NOT NULL).
    """
    lat = obs.get("lat")
    lng = obs.get("lng")
    if lat is None or lng is None:
        return

    taxon_name = obs.get("taxon_name")
    if not taxon_name:
        return

    kingdom = normalize_kingdom(
        obs.get("iconic_taxon_name") or obs.get("metadata", {}).get("kingdom"),
        source=obs.get("source"),
    )
    org_source_id = _organism_source_id(obs)
    image_url = _first_photo_url(obs)
    properties = {
        "core_taxon_id": str(core_taxon_id) if core_taxon_id else None,
        "inat_observation_id": obs.get("source_id"),
        "place_guess": obs.get("metadata", {}).get("place_guess"),
        "quality_grade": obs.get("quality_grade"),
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO species.organisms (
                source, source_id, kingdom, scientific_name, common_name,
                rank, taxonomy_id, image_url, properties
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source, source_id) DO UPDATE SET
                kingdom = EXCLUDED.kingdom,
                scientific_name = EXCLUDED.scientific_name,
                common_name = COALESCE(EXCLUDED.common_name, species.organisms.common_name),
                image_url = COALESCE(EXCLUDED.image_url, species.organisms.image_url),
                properties = species.organisms.properties || EXCLUDED.properties
            RETURNING id
            """,
            (
                obs.get("source", "inat"),
                org_source_id,
                kingdom,
                taxon_name,
                obs.get("taxon_common_name"),
                obs.get("taxon_rank", "species"),
                obs.get("taxon_inat_id"),
                image_url,
                json.dumps({k: v for k, v in properties.items() if v is not None}),
            ),
        )
        org_row = cur.fetchone()
        if not org_row:
            return
        organism_id = org_row["id"]

        cur.execute(
            """
            INSERT INTO species.sightings (
                organism_id, source, source_id, location, observed_at,
                observer, image_url, quality_grade, properties
            )
            SELECT %s, %s, %s,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                   %s::timestamptz, %s, %s, %s, %s::jsonb
            WHERE NOT EXISTS (
                SELECT 1 FROM species.sightings
                WHERE source = %s AND source_id = %s
            )
            """,
            (
                organism_id,
                obs.get("source", "inat"),
                obs.get("source_id"),
                lng,
                lat,
                obs.get("observed_at"),
                obs.get("observer"),
                image_url,
                obs.get("quality_grade"),
                json.dumps(obs.get("metadata") or {}),
                obs.get("source", "inat"),
                obs.get("source_id"),
            ),
        )
