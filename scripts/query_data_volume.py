#!/usr/bin/env python
"""
MINDEX Data Volume Query Script
================================
Shows comprehensive statistics about the fungal database.
"""
import sys
import json
from datetime import datetime
from typing import Dict, Any

sys.path.insert(0, '/app')

from mindex_etl.db import db_session


def format_number(num: int) -> str:
    """Format number with commas."""
    return f"{num:,}"


def get_statistics() -> Dict[str, Any]:
    """Get comprehensive database statistics."""
    stats = {}
    
    with db_session() as conn:
        with conn.cursor() as cur:
            # Total counts
            cur.execute("SELECT count(*) FROM core.taxon")
            stats["total_taxa"] = cur.fetchone()["count"]
            
            cur.execute("SELECT count(*) FROM obs.observation")
            stats["total_observations"] = cur.fetchone()["count"]
            
            cur.execute("SELECT count(*) FROM core.taxon_external_id")
            stats["total_external_ids"] = cur.fetchone()["count"]
            
            # Taxa by source
            cur.execute("""
                SELECT source, count(*) as count 
                FROM core.taxon 
                GROUP BY source 
                ORDER BY count DESC
            """)
            stats["taxa_by_source"] = {row["source"]: row["count"] for row in cur.fetchall()}
            
            # Observations by source
            cur.execute("""
                SELECT source, count(*) as count 
                FROM obs.observation 
                GROUP BY source 
                ORDER BY count DESC
            """)
            stats["observations_by_source"] = {row["source"]: row["count"] for row in cur.fetchall()}
            
            # Observations with location data
            cur.execute("""
                SELECT count(*) as count 
                FROM obs.observation 
                WHERE location IS NOT NULL
            """)
            stats["observations_with_location"] = cur.fetchone()["count"]
            
            # Observations with images
            cur.execute("""
                SELECT count(*) as count 
                FROM obs.observation 
                WHERE media IS NOT NULL 
                AND media::text != '[]'
            """)
            stats["observations_with_images"] = cur.fetchone()["count"]
            
            # Unique taxa with observations
            cur.execute("""
                SELECT count(DISTINCT taxon_id) as count 
                FROM obs.observation
            """)
            stats["taxa_with_observations"] = cur.fetchone()["count"]
            
            # Top taxa by observation count
            cur.execute("""
                SELECT 
                    t.canonical_name,
                    t.common_name,
                    count(o.id) as obs_count
                FROM core.taxon t
                JOIN obs.observation o ON o.taxon_id = t.id
                GROUP BY t.id, t.canonical_name, t.common_name
                ORDER BY obs_count DESC
                LIMIT 10
            """)
            stats["top_taxa_by_observations"] = [
                {
                    "name": row["canonical_name"],
                    "common_name": row["common_name"],
                    "observations": row["obs_count"]
                }
                for row in cur.fetchall()
            ]
            
            # Date range of observations
            cur.execute("""
                SELECT 
                    min(observed_at) as earliest,
                    max(observed_at) as latest
                FROM obs.observation
                WHERE observed_at IS NOT NULL
            """)
            date_range = cur.fetchone()
            if date_range and date_range["earliest"]:
                stats["observation_date_range"] = {
                    "earliest": date_range["earliest"].isoformat() if date_range["earliest"] else None,
                    "latest": date_range["latest"].isoformat() if date_range["latest"] else None,
                }
            
            # Genome records
            cur.execute("SELECT count(*) FROM bio.genome")
            stats["genome_records"] = cur.fetchone()["count"]
            
            # Taxon traits
            cur.execute("SELECT count(*) FROM bio.taxon_trait")
            stats["trait_records"] = cur.fetchone()["count"]
            
            # Taxon synonyms
            cur.execute("SELECT count(*) FROM core.taxon_synonym")
            stats["synonym_records"] = cur.fetchone()["count"]
    
    return stats


def print_statistics(stats: Dict[str, Any]) -> None:
    """Print formatted statistics."""
    print("=" * 70)
    print("MINDEX FUNGAL DATABASE STATISTICS")
    print("=" * 70)
    print(f"Generated: {datetime.now().isoformat()}")
    print()
    
    print("📊 CORE METRICS")
    print("-" * 70)
    print(f"  Total Taxa:              {format_number(stats['total_taxa'])}")
    print(f"  Total Observations:      {format_number(stats['total_observations'])}")
    print(f"  External ID Links:       {format_number(stats['total_external_ids'])}")
    print()
    
    print("📁 DATA BY SOURCE")
    print("-" * 70)
    print("  Taxa:")
    for source, count in stats["taxa_by_source"].items():
        print(f"    {source:20} {format_number(count)}")
    print()
    print("  Observations:")
    for source, count in stats["observations_by_source"].items():
        print(f"    {source:20} {format_number(count)}")
    print()
    
    print("📍 OBSERVATION QUALITY")
    print("-" * 70)
    print(f"  With Location Data:      {format_number(stats['observations_with_location'])}")
    print(f"  With Images:             {format_number(stats['observations_with_images'])}")
    print(f"  Unique Taxa Observed:    {format_number(stats['taxa_with_observations'])}")
    print()
    
    if stats.get("observation_date_range"):
        print("📅 OBSERVATION DATE RANGE")
        print("-" * 70)
        date_range = stats["observation_date_range"]
        if date_range["earliest"]:
            print(f"  Earliest: {date_range['earliest']}")
        if date_range["latest"]:
            print(f"  Latest:   {date_range['latest']}")
        print()
    
    print("🧬 ADDITIONAL DATA")
    print("-" * 70)
    print(f"  Genome Records:          {format_number(stats.get('genome_records', 0))}")
    print(f"  Trait Records:           {format_number(stats.get('trait_records', 0))}")
    print(f"  Synonym Records:         {format_number(stats.get('synonym_records', 0))}")
    print()
    
    if stats.get("top_taxa_by_observations"):
        print("🏆 TOP 10 TAXA BY OBSERVATIONS")
        print("-" * 70)
        for i, taxon in enumerate(stats["top_taxa_by_observations"], 1):
            common = f" ({taxon['common_name']})" if taxon["common_name"] else ""
            print(f"  {i:2}. {taxon['name']:40} {common:30} {format_number(taxon['observations'])} obs")
        print()
    
    print("=" * 70)


def main():
    """Main entry point."""
    try:
        stats = get_statistics()
        print_statistics(stats)
        
        # Also output JSON for programmatic access
        if "--json" in sys.argv:
            print("\n" + json.dumps(stats, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
