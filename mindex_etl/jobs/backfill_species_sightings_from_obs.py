"""
Backfill species.organisms + species.sightings from obs.observation (iNat rows).
Run once after deploying species_map_sync, or periodically to heal drift.

Usage:
    python -m mindex_etl.jobs.backfill_species_sightings_from_obs --limit 50000
"""
from __future__ import annotations

import argparse

from ..db import db_session
from .species_map_sync import upsert_species_map_rows


def backfill_species_sightings_from_obs(*, limit: int = 50000, source: str = "inat") -> int:
    synced = 0
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.source, o.source_id, o.observer, o.observed_at,
                       ST_Y(o.location::geometry) AS lat,
                       ST_X(o.location::geometry) AS lng,
                       o.accuracy_m, o.metadata, o.media, o.taxon_id,
                       t.canonical_name AS taxon_name,
                       t.rank AS taxon_rank,
                       t.common_name AS taxon_common_name,
                       t.kingdom AS iconic_taxon_name,
                       t.metadata->>'inat_id' AS taxon_inat_id
                FROM obs.observation o
                LEFT JOIN core.taxon t ON t.id = o.taxon_id
                WHERE o.source = %s
                  AND o.location IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM species.sightings s
                      WHERE s.source = o.source AND s.source_id = o.source_id
                  )
                ORDER BY o.observed_at DESC NULLS LAST
                LIMIT %s
                """,
                (source, limit),
            )
            rows = cur.fetchall()

        for row in rows:
            meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
            media = row["media"] if isinstance(row["media"], list) else []
            obs = {
                "source": row["source"],
                "source_id": row["source_id"],
                "observer": row["observer"],
                "observed_at": row["observed_at"],
                "lat": row["lat"],
                "lng": row["lng"],
                "accuracy_m": row["accuracy_m"],
                "taxon_name": row["taxon_name"] or meta.get("taxon_name"),
                "taxon_rank": row["taxon_rank"] or meta.get("taxon_rank", "species"),
                "taxon_common_name": row["taxon_common_name"] or meta.get("taxon_common_name"),
                "taxon_inat_id": row["taxon_inat_id"] or meta.get("taxon_inat_id"),
                "iconic_taxon_name": row["iconic_taxon_name"] or meta.get("iconic_taxon_name"),
                "quality_grade": meta.get("quality_grade"),
                "metadata": meta,
                "photos": media or meta.get("photos", []),
            }
            if not obs.get("taxon_name"):
                continue
            upsert_species_map_rows(conn, obs, core_taxon_id=row.get("taxon_id"))
            synced += 1

    return synced


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill species map tables from obs.observation")
    parser.add_argument("--limit", type=int, default=50000)
    parser.add_argument("--source", default="inat")
    args = parser.parse_args()
    count = backfill_species_sightings_from_obs(limit=args.limit, source=args.source)
    print(f"Backfilled {count} sightings from obs.observation", flush=True)


if __name__ == "__main__":
    main()
