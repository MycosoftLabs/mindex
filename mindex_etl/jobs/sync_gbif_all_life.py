"""
GBIF sync for all life or fungi — uses gbifDomainMode in config and gbif._fetch_species_page.
"""
from __future__ import annotations

import argparse
import time
from typing import Optional
from uuid import UUID

import httpx

from ..config import settings
from ..sources import gbif as gbif_source
from ..db import db_session
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def _kingdom_from_record(rec: dict) -> str | None:
    k = (rec.get("kingdom") or "").strip()
    if not k:
        return None
    allowed = {
        "Fungi", "Plantae", "Animalia", "Bacteria", "Archaea",
        "Protista", "Chromista", "Viruses", "Undesignated",
    }
    if k in allowed:
        return "Protista" if k == "Chromista" else k
    return "Undesignated"


def sync_gbif_all_life(
    *,
    limit: int = 300,
    max_offset: Optional[int] = None,
    delay: float = 0.35,
    domain_mode: Optional[str] = None,
) -> int:
    mode = (domain_mode or settings.gbif_domain_mode or "all").strip().lower()
    print("=" * 60)
    print(f"GBIF SYNC (domain_mode={mode})")
    print("=" * 60)
    processed = 0
    errors = 0
    offset = 0
    with httpx.Client() as client:
        with db_session() as conn:
            while True:
                try:
                    data = gbif_source._fetch_species_page(
                        client, offset, limit, domain_mode=mode
                    )
                except Exception as e:
                    print(f"Error at offset {offset}: {e}")
                    errors += 1
                    if errors > 10:
                        break
                    offset += limit
                    continue
                results = data.get("results") or []
                if not results:
                    break
                for record in results:
                    try:
                        canonical_name = record.get("canonicalName")
                        if not canonical_name:
                            continue
                        kingdom = _kingdom_from_record(record)
                        taxon_payload = {
                            "canonical_name": canonical_name,
                            "rank": (record.get("rank") or "species").lower(),
                            "source": "gbif",
                            "kingdom": kingdom,
                            "metadata": {
                                "gbif_key": record.get("key"),
                                "nub_key": record.get("nubKey"),
                                "scientific_name": record.get("scientificName"),
                                "authorship": record.get("authorship"),
                                "kingdom": record.get("kingdom"),
                                "phylum": record.get("phylum"),
                                "class": record.get("class"),
                                "order": record.get("order"),
                                "family": record.get("family"),
                                "genus": record.get("genus"),
                            },
                        }
                        taxon_id = upsert_taxon(conn, **taxon_payload)
                        if record.get("key"):
                            link_external_id(
                                conn,
                                taxon_id=UUID(str(taxon_id)) if not isinstance(taxon_id, UUID) else taxon_id,
                                source="gbif",
                                external_id=str(record["key"]),
                                metadata=taxon_payload["metadata"],
                            )
                        processed += 1
                    except Exception as ex:
                        errors += 1
                        if errors <= 5:
                            print(f"row error: {ex}")
                conn.commit()
                if data.get("endOfRecords"):
                    break
                if max_offset is not None and offset >= max_offset:
                    break
                offset += limit
                time.sleep(delay)
    print(f"Done. processed={processed} errors>={errors}")
    return processed


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max-offset", type=int, default=None)
    p.add_argument("--limit", type=int, default=300)
    p.add_argument(
        "--domain",
        type=str,
        default=None,
        help="Override gbif_domain_mode: all | fungi",
    )
    args = p.parse_args()
    sync_gbif_all_life(
        limit=args.limit,
        max_offset=args.max_offset,
        domain_mode=args.domain,
    )


if __name__ == "__main__":
    main()
