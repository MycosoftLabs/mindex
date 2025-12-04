from __future__ import annotations

import argparse
import json
from typing import Dict

from ..db import db_session
from ..sources import mushroom_world, wikipedia
from ..taxon_canonicalizer import upsert_taxon


def _insert_trait(conn, taxon_id, trait_name: str, value_text: str, source: str, metadata: Dict | None = None) -> None:
    metadata = metadata or {}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bio.taxon_trait (taxon_id, trait_name, value_text, source, metadata)
            SELECT %s, %s, %s, %s, %s::jsonb
            WHERE NOT EXISTS (
                SELECT 1 FROM bio.taxon_trait WHERE taxon_id = %s AND trait_name = %s AND value_text = %s
            )
            """,
            (taxon_id, trait_name, value_text, source, json.dumps(metadata), taxon_id, trait_name, value_text),
        )


def backfill_traits(*, max_pages: int | None = None, enrich_wikipedia: bool = True) -> int:
    processed = 0
    with db_session() as conn:
        for record in mushroom_world.iter_mushroom_world_species(max_pages=max_pages):
            taxon_name = record.get("canonical_name")
            if not taxon_name:
                continue
            taxon_id = upsert_taxon(
                conn,
                canonical_name=taxon_name,
                rank=record.get("rank", "species"),
                common_name=record.get("common_name"),
                description=record.get("description"),
                source="mushroom_world",
                metadata=record.get("metadata", {}),
            )
            for trait in record.get("traits", []):
                if trait.get("trait_name") and trait.get("value_text"):
                    _insert_trait(
                        conn,
                        taxon_id,
                        trait_name=trait["trait_name"],
                        value_text=trait["value_text"],
                        source="mushroom_world",
                        metadata={},
                    )
            if enrich_wikipedia:
                summary = wikipedia.fetch_page_summary(taxon_name)
                if summary:
                    extracted = wikipedia.extract_traits(summary)
                    for trait_name, value in extracted.items():
                        _insert_trait(
                            conn,
                            taxon_id,
                            trait_name=trait_name,
                            value_text=value,
                            source="wikipedia",
                            metadata={"page": summary.get("title")},
                        )
            processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill taxon traits from Mushroom.World and Wikipedia")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--skip-wikipedia", action="store_true")
    args = parser.parse_args()
    total = backfill_traits(max_pages=args.max_pages, enrich_wikipedia=not args.skip_wikipedia)
    print(f"Backfilled traits for {total} taxa")


if __name__ == "__main__":
    main()
