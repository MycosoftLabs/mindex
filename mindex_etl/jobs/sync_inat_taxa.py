from __future__ import annotations

import argparse

from ..db import db_session
from ..sources import inat
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def sync_inat_taxa(*, per_page: int = 100, max_pages: int | None = None) -> int:
    created = 0
    with db_session() as conn:
        for taxon_payload, source, external_id in inat.iter_fungi_taxa(
            per_page=per_page,
            max_pages=max_pages,
        ):
            taxon_id = upsert_taxon(conn, **taxon_payload)
            link_external_id(
                conn,
                taxon_id=taxon_id,
                source=source,
                external_id=external_id,
                metadata={"source": source},
            )
            created += 1
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync iNaturalist fungal taxa into MINDEX")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()
    total = sync_inat_taxa(per_page=args.per_page, max_pages=args.max_pages)
    print(f"Synced {total} iNaturalist taxa")


if __name__ == "__main__":
    main()
