from __future__ import annotations

import argparse
from typing import Optional

from ..checkpoint import CheckpointManager
from ..config import settings
from ..db import db_session
from ..sources import inat
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def sync_inat_taxa(
    *,
    per_page: int = 100,
    max_pages: int | None = None,
    start_page: int = 1,
    checkpoint_manager: Optional[CheckpointManager] = None,
    domain_mode: Optional[str] = None,
) -> int:
    """Sync iNaturalist taxa with checkpoint support. domain_mode: 'all' or 'fungi' (default from config)."""
    mode = domain_mode or settings.inat_domain_mode
    inat._validate_page_arguments(per_page, start_page, max_pages)
    per_page = min(per_page, 200)
    query = {"source": "inat_taxa", "base_url": settings.inat_base_url,
             "taxon_id": inat._root_taxon_id(mode), "per_page": per_page,
             "is_active": True, "order_by": "observations_count", "rank": None}
    if checkpoint_manager is not None:
        # Bound to the configured destination as well as the source query. Only
        # the combined fingerprint is persisted, never the raw connection URL.
        query["database_target"] = settings.database_url
        start_page = checkpoint_manager.resume_page(query=query, start_page=start_page)
    created = 0
    
    with db_session() as conn:
        def committed_page(page: int) -> None:
            conn.commit()
            if checkpoint_manager is not None:
                checkpoint_manager.save_committed(page, query=query, records_processed=created)

        for taxon_payload, source, external_id in inat.iter_inat_taxa(
            per_page=per_page,
            max_pages=max_pages,
            domain_mode=mode,
            start_page=start_page,
            on_page=committed_page,
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
    parser = argparse.ArgumentParser(description="Sync iNaturalist taxa into MINDEX")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--domain-mode", type=str, default=None, choices=["all", "fungi"],
                        help="'all' for all life, 'fungi' for fungi-only (default from config)")
    args = parser.parse_args()
    total = sync_inat_taxa(per_page=args.per_page, max_pages=args.max_pages, domain_mode=args.domain_mode)
    print(f"Synced {total} iNaturalist taxa")


if __name__ == "__main__":
    main()
