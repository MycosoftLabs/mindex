"""
MINDEX Species Data Completeness Job
====================================
Scans all species in the database and flags those with missing data:
- No images (default_photo in metadata)
- No description
- No genetics data (genetic_sequence records)
- No chemistry data (optional - compound relationships)

Used by auto_enrich_species and Ancestry data quality dashboard.

Run:
    python -m mindex_etl.jobs.species_data_completeness
    python -m mindex_etl.jobs.species_data_completeness --json
    python -m mindex_etl.jobs.species_data_completeness --limit 100 --incomplete-only
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from ..db import db_session


def get_species_completeness(
    limit: Optional[int] = None,
    incomplete_only: bool = False,
    rank_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scan species and compute data completeness stats.
    
    Returns dict with:
        total: total species count
        with_images: count with default_photo
        with_description: count with non-null description
        with_genetics: count with genetic_sequence records
        incomplete: list of incomplete species (id, canonical_name, missing_fields)
        stats: aggregate counts
    """
    rank_filter = rank_filter or "species"
    
    with db_session() as conn:
        with conn.cursor() as cur:
            # Base query for species
            base_conditions = ["rank = %s"]
            params: List[Any] = [rank_filter]
            
            if incomplete_only:
                base_conditions.append("""
                    (
                        (metadata->>'default_photo') IS NULL
                        OR (metadata->'default_photo'->>'url') IS NULL
                        OR (metadata->'default_photo'->>'url') = ''
                        OR description IS NULL OR TRIM(description) = ''
                        OR id NOT IN (SELECT taxon_id FROM bio.genetic_sequence WHERE taxon_id IS NOT NULL)
                    )
                """)
            
            where_clause = " AND ".join(base_conditions)
            
            # Total species count
            cur.execute(
                f"SELECT COUNT(*) AS c FROM core.taxon WHERE {where_clause}",
                params,
            )
            total = cur.fetchone()["c"]
            
            # With images
            cur.execute(
                f"""
                SELECT COUNT(*) AS c FROM core.taxon
                WHERE {where_clause}
                AND metadata->'default_photo'->>'url' IS NOT NULL
                AND (metadata->'default_photo'->>'url') != ''
                """,
                params,
            )
            with_images = cur.fetchone()["c"]
            
            # With description
            cur.execute(
                f"""
                SELECT COUNT(*) AS c FROM core.taxon
                WHERE {where_clause}
                AND description IS NOT NULL AND TRIM(description) != ''
                """,
                params,
            )
            with_description = cur.fetchone()["c"]
            
            # With genetics (has genetic_sequence records)
            cur.execute(
                """
                SELECT COUNT(DISTINCT t.id) AS c
                FROM core.taxon t
                INNER JOIN bio.genetic_sequence gs ON gs.taxon_id = t.id
                WHERE t.rank = %s
                """,
                [rank_filter],
            )
            with_genetics = cur.fetchone()["c"]
            
            # Fetch incomplete species list (id, canonical_name, missing flags)
            params_list = params + [limit] if limit else params
            limit_clause = " LIMIT %s" if limit else ""
            
            cur.execute(
                f"""
                SELECT
                    t.id,
                    t.canonical_name,
                    CASE WHEN (t.metadata->'default_photo'->>'url') IS NULL 
                         OR (t.metadata->'default_photo'->>'url') = '' THEN true ELSE false END AS missing_image,
                    CASE WHEN t.description IS NULL OR TRIM(t.description) = '' THEN true ELSE false END AS missing_description,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM bio.genetic_sequence gs WHERE gs.taxon_id = t.id
                    ) THEN false ELSE true END AS missing_genetics
                FROM core.taxon t
                WHERE {where_clause}
                ORDER BY t.canonical_name
                {limit_clause}
                """,
                params_list,
            )
            rows = cur.fetchall()
    
    incomplete = []
    for row in rows:
        missing = []
        if row.get("missing_image"):
            missing.append("image")
        if row.get("missing_description"):
            missing.append("description")
        if row.get("missing_genetics"):
            missing.append("genetics")
        if missing:
            incomplete.append({
                "id": str(row["id"]),
                "canonical_name": row["canonical_name"],
                "missing": missing,
            })
    
    return {
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "total": total,
        "with_images": with_images,
        "with_description": with_description,
        "with_genetics": with_genetics,
        "incomplete_count": len(incomplete),
        "incomplete": incomplete[:500],  # Cap list for JSON size
        "stats": {
            "total_species": total,
            "with_images": with_images,
            "with_description": with_description,
            "with_genetics": with_genetics,
            "missing_images": total - with_images,
            "missing_description": total - with_description,
            "missing_genetics": total - with_genetics,
        },
    }


def save_report(output_path: Optional[Path] = None) -> Path:
    """Run scan and save report to data dir."""
    result = get_species_completeness(limit=None, incomplete_only=False)
    out = output_path or Path(settings.local_data_dir) / "species_completeness"
    out.mkdir(parents=True, exist_ok=True)
    filepath = out / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return filepath


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MINDEX Species Data Completeness Scan"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit incomplete species list to N entries",
    )
    parser.add_argument(
        "--incomplete-only",
        action="store_true",
        help="Only report incomplete species",
    )
    parser.add_argument(
        "--rank",
        type=str,
        default="species",
        help="Taxon rank to scan (default: species)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON to stdout",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save report to data dir",
    )
    
    args = parser.parse_args()
    
    result = get_species_completeness(
        limit=args.limit,
        incomplete_only=args.incomplete_only,
        rank_filter=args.rank,
    )
    
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    if args.save:
        path = save_report()
        print(f"Report saved to {path}")
    
    if not args.json and not args.save:
        # Human-readable summary
        s = result["stats"]
        print("\n" + "=" * 60)
        print("MINDEX Species Data Completeness")
        print("=" * 60)
        print(f"Total species:        {s['total_species']:,}")
        print(f"With images:          {s['with_images']:,}")
        print(f"With description:     {s['with_description']:,}")
        print(f"With genetics:        {s['with_genetics']:,}")
        print(f"Missing images:       {s['missing_images']:,}")
        print(f"Missing description:  {s['missing_description']:,}")
        print(f"Missing genetics:     {s['missing_genetics']:,}")
        print("=" * 60)
        if result.get("incomplete"):
            print(f"\nIncomplete species (first 20):")
            for item in result["incomplete"][:20]:
                print(f"  - {item['canonical_name']}: missing {', '.join(item['missing'])}")
        print()


if __name__ == "__main__":
    main()
