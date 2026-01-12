from __future__ import annotations

"""
Backfill iNaturalist default photos into core.taxon.metadata

Why:
- The website should NEVER live-scrape images.
- MINDEX should store a "best" image per taxon (at minimum iNat default_photo),
  so Explorer/Database pages can render instantly and consistently.

This job:
- Selects iNat taxa already in MINDEX (source='inat') ordered by observations_count.
- Fetches the taxon's default_photo from iNaturalist /v1/taxa/{id}.
- Writes it into core.taxon.metadata.default_photo (jsonb).

Run (inside mindex-api container):
  python -m mindex_etl.jobs.backfill_inat_taxon_photos --limit 1000
"""

import argparse
import json
import time
from typing import Any, Optional

import httpx

from ..config import settings
from ..db import db_session
from ..sources.inat import get_auth_headers


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def backfill_inat_taxon_photos(*, limit: int = 1000, delay_seconds: float = 0.2) -> int:
    updated = 0

    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    canonical_name,
                    (metadata->>'inat_id') AS inat_id,
                    (metadata->>'observations_count') AS observations_count
                FROM core.taxon
                WHERE source = 'inat'
                  AND (metadata->>'inat_id') IS NOT NULL
                ORDER BY
                  CASE
                    WHEN (metadata->>'observations_count') ~ '^[0-9]+$' THEN (metadata->>'observations_count')::int
                    ELSE 0
                  END DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    if not rows:
        return 0

    headers = get_auth_headers()
    with httpx.Client(timeout=settings.http_timeout, headers=headers) as client:
        with db_session() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    taxon_id = row["id"]
                    canonical_name = row["canonical_name"]
                    inat_id = row["inat_id"]
                    obs = _safe_int(row["observations_count"])

                    try:
                        resp = client.get(f"{settings.inat_base_url}/taxa/{inat_id}")
                        resp.raise_for_status()
                        payload = resp.json()
                        result = (payload.get("results") or [None])[0] or {}
                        default_photo = result.get("default_photo")
                        if not default_photo:
                            continue

                        cur.execute(
                            """
                            UPDATE core.taxon
                            SET metadata = jsonb_set(
                                COALESCE(metadata, '{}'::jsonb),
                                '{default_photo}',
                                %s::jsonb,
                                true
                            ),
                            updated_at = now()
                            WHERE id = %s
                            """,
                            (json.dumps(default_photo), taxon_id),
                        )
                        updated += 1

                        if updated % 50 == 0:
                            conn.commit()
                            print(
                                f"Backfilled {updated}/{len(rows)} (latest: {canonical_name}, obs={obs})",
                                flush=True,
                            )

                        time.sleep(delay_seconds)
                    except Exception as e:
                        print(f"Failed {canonical_name} (inat_id={inat_id}): {e}", flush=True)
                        time.sleep(1.0)
                        continue

            conn.commit()

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill iNaturalist default_photo into MINDEX taxa metadata")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--delay-seconds", type=float, default=0.2)
    args = parser.parse_args()

    total = backfill_inat_taxon_photos(limit=args.limit, delay_seconds=args.delay_seconds)
    print(f"Backfilled default_photo for {total} taxa")


if __name__ == "__main__":
    main()

