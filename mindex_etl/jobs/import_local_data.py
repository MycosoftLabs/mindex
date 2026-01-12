"""
Import locally downloaded data into MINDEX database.

This script imports JSON files from the local scrape directory into the database.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import settings
from ..db import db_session
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def import_gbif_data(filepath: str | Path, max_records: Optional[int] = None) -> int:
    """Import GBIF data from a JSON file."""
    filepath = Path(filepath)
    
    print(f"Importing GBIF data from {filepath}")
    
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    records = data.get("records", [])
    total = len(records)
    
    print(f"Found {total} records")
    
    if max_records:
        records = records[:max_records]
        print(f"Limiting to {max_records} records")
    
    imported = 0
    skipped = 0
    errors = 0
    
    with db_session() as conn:
        for i, record in enumerate(records):
            try:
                # Map GBIF format to MINDEX format
                taxon_payload = {
                    "canonical_name": record.get("canonical_name"),
                    "rank": record.get("rank", "species"),
                    "common_name": None,  # GBIF doesn't provide common names in species search
                    "description": None,
                    "source": "gbif",
                    "metadata": {
                        "gbif_key": record.get("species_key"),
                        "nub_key": record.get("nub_key"),
                        "kingdom": record.get("kingdom"),
                        "phylum": record.get("phylum"),
                        "class": record.get("class"),
                        "order": record.get("order"),
                        "family": record.get("family"),
                        "genus": record.get("genus"),
                        "scientific_name": record.get("scientific_name"),
                    }
                }
                
                if not taxon_payload["canonical_name"]:
                    skipped += 1
                    continue
                
                taxon_id = upsert_taxon(conn, **taxon_payload)
                
                # Link external ID
                if record.get("species_key"):
                    link_external_id(
                        conn,
                        taxon_id=taxon_id,
                        source="gbif",
                        external_id=str(record["species_key"]),
                        metadata=taxon_payload["metadata"],
                    )
                
                imported += 1
                
                if imported % 500 == 0:
                    conn.commit()
                    print(f"  Imported {imported}/{len(records)}...", flush=True)
                    
            except Exception as e:
                errors += 1
                if errors <= 5:  # Only print first 5 errors
                    print(f"  Error importing {record.get('canonical_name', 'unknown')}: {e}")
        
        conn.commit()
    
    print(f"\nComplete: Imported {imported}, Skipped {skipped}, Errors {errors}")
    return imported


def import_inaturalist_data(filepath: str | Path, max_records: Optional[int] = None) -> int:
    """Import iNaturalist data from a JSON file."""
    filepath = Path(filepath)
    
    print(f"Importing iNaturalist data from {filepath}")
    
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Handle different formats
    if "records" in data:
        records = data["records"]
    elif "taxa" in data:
        records = data["taxa"]
    else:
        records = data if isinstance(data, list) else []
    
    total = len(records)
    print(f"Found {total} records")
    
    if max_records:
        records = records[:max_records]
        print(f"Limiting to {max_records} records")
    
    imported = 0
    skipped = 0
    errors = 0
    
    with db_session() as conn:
        for i, record in enumerate(records):
            try:
                # Handle nested structure
                if "taxon" in record:
                    taxon_data = record["taxon"]
                    external_id = record.get("external_id")
                else:
                    taxon_data = record
                    external_id = record.get("metadata", {}).get("inat_id")
                
                taxon_payload = {
                    "canonical_name": taxon_data.get("canonical_name"),
                    "rank": taxon_data.get("rank", "species"),
                    "common_name": taxon_data.get("common_name"),
                    "description": taxon_data.get("description"),
                    "source": "inat",
                    "metadata": taxon_data.get("metadata", {}),
                }
                
                if not taxon_payload["canonical_name"]:
                    skipped += 1
                    continue
                
                taxon_id = upsert_taxon(conn, **taxon_payload)
                
                # Link external ID
                if external_id:
                    link_external_id(
                        conn,
                        taxon_id=taxon_id,
                        source="inat",
                        external_id=str(external_id),
                        metadata=taxon_payload["metadata"],
                    )
                
                imported += 1
                
                if imported % 500 == 0:
                    conn.commit()
                    print(f"  Imported {imported}/{len(records)}...", flush=True)
                    
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Error importing {taxon_data.get('canonical_name', 'unknown')}: {e}")
        
        conn.commit()
    
    print(f"\nComplete: Imported {imported}, Skipped {skipped}, Errors {errors}")
    return imported


def import_all_local_data(data_dir: str | Path = None) -> dict:
    """Import all locally downloaded data into MINDEX."""
    data_dir = Path(data_dir or settings.local_data_dir)
    
    print("="*60)
    print("IMPORTING ALL LOCAL DATA INTO MINDEX")
    print("="*60)
    print(f"Data directory: {data_dir}")
    print()
    
    stats = {
        "start_time": datetime.now().isoformat(),
        "sources": {},
        "total_imported": 0,
    }
    
    # Import GBIF
    gbif_files = list((data_dir / "gbif").glob("*.json")) if (data_dir / "gbif").exists() else []
    for filepath in gbif_files:
        try:
            count = import_gbif_data(filepath)
            stats["sources"]["gbif"] = count
            stats["total_imported"] += count
        except Exception as e:
            print(f"Error importing {filepath}: {e}")
    
    # Import iNaturalist
    inat_dirs = ["inaturalist", "inat"]
    for inat_dir in inat_dirs:
        inat_path = data_dir / inat_dir
        if inat_path.exists():
            for filepath in inat_path.glob("*.json"):
                try:
                    count = import_inaturalist_data(filepath)
                    stats["sources"]["inat"] = stats["sources"].get("inat", 0) + count
                    stats["total_imported"] += count
                except Exception as e:
                    print(f"Error importing {filepath}: {e}")
    
    stats["end_time"] = datetime.now().isoformat()
    
    print()
    print("="*60)
    print("IMPORT COMPLETE")
    print("="*60)
    print(f"Total imported: {stats['total_imported']}")
    for source, count in stats["sources"].items():
        print(f"  {source}: {count}")
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Import local data into MINDEX")
    parser.add_argument(
        "--file", "-f",
        help="Specific file to import",
    )
    parser.add_argument(
        "--source", "-s",
        choices=["gbif", "inat", "mycobank", "all"],
        default="all",
        help="Data source type",
    )
    parser.add_argument(
        "--data-dir", "-d",
        default=None,
        help="Data directory",
    )
    parser.add_argument(
        "--max-records", "-m",
        type=int,
        default=None,
        help="Maximum records to import",
    )
    
    args = parser.parse_args()
    
    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"File not found: {filepath}")
            return
        
        if args.source == "gbif" or "gbif" in str(filepath).lower():
            import_gbif_data(filepath, args.max_records)
        elif args.source == "inat" or "inat" in str(filepath).lower():
            import_inaturalist_data(filepath, args.max_records)
        else:
            print(f"Unknown source for file: {filepath}")
    else:
        import_all_local_data(args.data_dir)


if __name__ == "__main__":
    main()
