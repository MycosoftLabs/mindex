from __future__ import annotations

import argparse
from typing import Optional

from ..checkpoint import CheckpointManager
from ..db import db_session
from ..sources import inat
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def sync_inat_taxa(
    *,
    per_page: int = 100,
    max_pages: int | None = None,
    start_page: int = 1,
    checkpoint_manager: Optional[CheckpointManager] = None,
) -> int:
    """Sync iNaturalist taxa with checkpoint support."""
    created = 0
    checkpoint_interval = 10  # Save checkpoint every 10 pages
    
    with db_session() as conn:
        page = start_page
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
            
            # Save checkpoint periodically
            if checkpoint_manager and created % (per_page * checkpoint_interval) == 0:
                checkpoint_manager.save(page, records_processed=created)
                print(f"Checkpoint saved: page {page}, {created} records", flush=True)
            
            # Track current page (approximate)
            if created % per_page == 0:
                page += 1
    
    # Final checkpoint
    if checkpoint_manager:
        checkpoint_manager.save(page, records_processed=created, completed=True)
    
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
