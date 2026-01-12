"""
MINDEX Master Data Downloader

Downloads ALL fungal data from ALL sources to local storage.
Modus operandi: Scrape EVERYTHING first, then organize/normalize locally.

Storage targets:
- Local: C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape (12TB)
- NAS: \\\\192.168.1.50\\mindex (16TB)
- Dream Machine: 27TB backup

Expected data:
- iNaturalist: 26,616+ species
- MycoBank: 545,007+ species
- GBIF: 50,000+ occurrences
- TheYeasts.org: 3,502 species
- Fusarium.org: 408 species
- Mushroom.World: 1,000+ species
- Index Fungorum: 10,000+ species
- FungiDB: 500+ genomes
- Wikipedia: Trait enrichment
- NCBI GenBank: Genetic sequences

TOTAL TARGET: 575,000+ unique fungal species
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from ..config import settings


class MasterDownloader:
    """
    Master data downloader for MINDEX.
    
    Downloads everything to local storage first, then processes.
    """
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir or settings.local_data_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.stats = {
            "start_time": None,
            "end_time": None,
            "sources": {},
            "total_records": 0,
            "total_bytes": 0,
            "errors": [],
        }
    
    def _save_json(self, data: dict, source: str, filename: str) -> str:
        """Save data as JSON to source-specific directory."""
        source_dir = self.output_dir / source
        source_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = source_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        size = filepath.stat().st_size
        self.stats["total_bytes"] += size
        
        return str(filepath)
    
    def _log(self, message: str):
        """Log with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}", flush=True)
    
    # =========================================================================
    # iNaturalist Download
    # =========================================================================
    
    def download_inaturalist(self) -> Dict:
        """Download all iNaturalist fungal taxa."""
        self._log("="*60)
        self._log("DOWNLOADING: iNaturalist")
        self._log("="*60)
        
        from . import inat
        
        records = []
        count = 0
        
        try:
            for taxon, source, ext_id in inat.iter_fungi_taxa(
                per_page=200,
                max_pages=None,
                save_locally=False,
            ):
                records.append({
                    "taxon": taxon,
                    "source": source,
                    "external_id": ext_id,
                })
                count += 1
                
                if count % 1000 == 0:
                    self._log(f"  iNaturalist: {count} records...")
        
        except Exception as e:
            self._log(f"  Error: {e}")
            self.stats["errors"].append(f"iNaturalist: {e}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"inaturalist_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "inaturalist",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "inaturalist", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["inaturalist"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # MycoBank Download
    # =========================================================================
    
    def download_mycobank(self) -> Dict:
        """Download all MycoBank fungal taxa."""
        self._log("="*60)
        self._log("DOWNLOADING: MycoBank")
        self._log("="*60)
        
        from . import mycobank
        
        records = []
        count = 0
        
        try:
            for taxon, synonyms, ext_id in mycobank.iter_mycobank_taxa(
                use_scraping=True,
                try_dump=True,
                save_locally=False,
            ):
                records.append({
                    "taxon": taxon,
                    "synonyms": synonyms,
                    "external_id": ext_id,
                })
                count += 1
                
                if count % 1000 == 0:
                    self._log(f"  MycoBank: {count} records...")
        
        except Exception as e:
            self._log(f"  Error: {e}")
            self.stats["errors"].append(f"MycoBank: {e}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mycobank_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "mycobank",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "mycobank", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["mycobank"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # GBIF Download
    # =========================================================================
    
    def download_gbif(self) -> Dict:
        """Download GBIF fungal occurrence data."""
        self._log("="*60)
        self._log("DOWNLOADING: GBIF")
        self._log("="*60)
        
        records = []
        count = 0
        
        try:
            with httpx.Client() as client:
                # GBIF Fungi kingdom key is 5
                offset = 0
                limit = 300
                
                while True:
                    response = client.get(
                        "https://api.gbif.org/v1/species/search",
                        params={
                            "highertaxonKey": 5,  # Fungi
                            "rank": "SPECIES",
                            "status": "ACCEPTED",
                            "limit": limit,
                            "offset": offset,
                        },
                        timeout=60.0,
                        headers={"User-Agent": "MINDEX-ETL/1.0"},
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    results = data.get("results", [])
                    
                    if not results:
                        break
                    
                    for record in results:
                        records.append({
                            "canonical_name": record.get("canonicalName"),
                            "scientific_name": record.get("scientificName"),
                            "rank": record.get("rank", "").lower(),
                            "kingdom": record.get("kingdom"),
                            "phylum": record.get("phylum"),
                            "class": record.get("class"),
                            "order": record.get("order"),
                            "family": record.get("family"),
                            "genus": record.get("genus"),
                            "species_key": record.get("speciesKey"),
                            "nub_key": record.get("nubKey"),
                        })
                        count += 1
                    
                    if count % 1000 == 0:
                        self._log(f"  GBIF: {count} records...")
                    
                    if data.get("endOfRecords", True):
                        break
                    
                    offset += limit
                    time.sleep(0.5)  # Rate limiting
        
        except Exception as e:
            self._log(f"  Error: {e}")
            self.stats["errors"].append(f"GBIF: {e}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gbif_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "gbif",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "gbif", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["gbif"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # TheYeasts Download
    # =========================================================================
    
    def download_theyeasts(self) -> Dict:
        """Download TheYeasts.org yeast species."""
        self._log("="*60)
        self._log("DOWNLOADING: TheYeasts.org")
        self._log("="*60)
        
        from . import theyeasts
        
        records = []
        count = 0
        
        try:
            for taxon, source, ext_id in theyeasts.iter_theyeasts_species(
                fetch_details=True,
                delay_seconds=1.0,
            ):
                records.append({
                    "taxon": taxon,
                    "source": source,
                    "external_id": ext_id,
                })
                count += 1
                
                if count % 100 == 0:
                    self._log(f"  TheYeasts: {count} records...")
        
        except Exception as e:
            self._log(f"  Error: {e}")
            self.stats["errors"].append(f"TheYeasts: {e}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"theyeasts_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "theyeasts",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "theyeasts", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["theyeasts"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # Fusarium Download
    # =========================================================================
    
    def download_fusarium(self) -> Dict:
        """Download Fusarium.org species."""
        self._log("="*60)
        self._log("DOWNLOADING: Fusarium.org")
        self._log("="*60)
        
        from . import fusarium
        
        records = []
        count = 0
        
        try:
            for taxon, source, ext_id in fusarium.iter_fusarium_species(
                fetch_details=True,
                delay_seconds=1.0,
            ):
                records.append({
                    "taxon": taxon,
                    "source": source,
                    "external_id": ext_id,
                })
                count += 1
        
        except Exception as e:
            self._log(f"  Error: {e}")
            # Try fallback
            try:
                self._log("  Trying fallback list...")
                for taxon, source, ext_id in fusarium.iter_fusarium_fallback():
                    records.append({
                        "taxon": taxon,
                        "source": source,
                        "external_id": ext_id,
                    })
                    count += 1
            except Exception as e2:
                self.stats["errors"].append(f"Fusarium: {e2}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"fusarium_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "fusarium",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "fusarium", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["fusarium"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # Mushroom.World Download
    # =========================================================================
    
    def download_mushroom_world(self) -> Dict:
        """Download Mushroom.World species."""
        self._log("="*60)
        self._log("DOWNLOADING: Mushroom.World")
        self._log("="*60)
        
        from . import mushroom_world
        
        records = []
        count = 0
        
        try:
            for taxon, source, ext_id in mushroom_world.iter_mushroom_world_species(
                fetch_details=True,
                delay_seconds=1.0,
            ):
                records.append({
                    "taxon": taxon,
                    "source": source,
                    "external_id": ext_id,
                })
                count += 1
                
                if count % 100 == 0:
                    self._log(f"  Mushroom.World: {count} records...")
        
        except Exception as e:
            self._log(f"  Error: {e}")
            self.stats["errors"].append(f"Mushroom.World: {e}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mushroom_world_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "mushroom_world",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "mushroom_world", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["mushroom_world"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # Index Fungorum Download (BONUS)
    # =========================================================================
    
    def download_index_fungorum(self) -> Dict:
        """Download Index Fungorum species."""
        self._log("="*60)
        self._log("DOWNLOADING: Index Fungorum")
        self._log("="*60)
        
        records = []
        count = 0
        
        try:
            with httpx.Client(follow_redirects=True) as client:
                # Index Fungorum has a simple search API
                for letter in "abcdefghijklmnopqrstuvwxyz":
                    self._log(f"  Searching '{letter}'...")
                    
                    response = client.get(
                        f"http://www.indexfungorum.org/Names/NamesResults.asp",
                        params={"Name": f"{letter}*"},
                        timeout=60.0,
                        headers={"User-Agent": "MINDEX-ETL/1.0"},
                    )
                    
                    if response.status_code != 200:
                        continue
                    
                    soup = BeautifulSoup(response.text, "html.parser")
                    
                    # Parse results table
                    for row in soup.select("table tr"):
                        cells = row.select("td")
                        if len(cells) < 2:
                            continue
                        
                        name_cell = cells[0]
                        name = name_cell.get_text(strip=True)
                        
                        if name and len(name) > 3:
                            records.append({
                                "canonical_name": name,
                                "source": "index_fungorum",
                            })
                            count += 1
                    
                    time.sleep(2.0)  # Be gentle
        
        except Exception as e:
            self._log(f"  Error: {e}")
            self.stats["errors"].append(f"Index Fungorum: {e}")
        
        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"index_fungorum_complete_{timestamp}.json"
        
        filepath = self._save_json({
            "source": "index_fungorum",
            "downloaded_at": datetime.now().isoformat(),
            "total_records": len(records),
            "records": records,
        }, "index_fungorum", filename)
        
        self._log(f"  Saved {count} records to {filepath}")
        
        self.stats["sources"]["index_fungorum"] = {
            "count": count,
            "filepath": filepath,
            "status": "success" if count > 0 else "partial",
        }
        
        return {"count": count, "filepath": filepath}
    
    # =========================================================================
    # MASTER DOWNLOAD: Everything
    # =========================================================================
    
    def download_all(self, parallel: bool = False) -> Dict:
        """
        Download ALL fungal data from ALL sources.
        
        Args:
            parallel: Run downloads in parallel (faster but more resource intensive)
        
        Returns:
            Complete statistics dictionary
        """
        self.stats["start_time"] = datetime.now().isoformat()
        
        self._log("="*60)
        self._log("MINDEX MASTER DATA DOWNLOAD")
        self._log("="*60)
        self._log(f"Output directory: {self.output_dir}")
        self._log("")
        
        sources = [
            ("iNaturalist", self.download_inaturalist),
            ("GBIF", self.download_gbif),
            ("MycoBank", self.download_mycobank),
            ("TheYeasts", self.download_theyeasts),
            ("Fusarium", self.download_fusarium),
            ("Mushroom.World", self.download_mushroom_world),
            ("Index Fungorum", self.download_index_fungorum),
        ]
        
        if parallel:
            # Parallel execution
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(fn): name 
                    for name, fn in sources
                }
                
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        result = future.result()
                        self._log(f"Completed: {name} ({result.get('count', 0)} records)")
                    except Exception as e:
                        self._log(f"Failed: {name} - {e}")
                        self.stats["errors"].append(f"{name}: {e}")
        else:
            # Sequential execution
            for name, fn in sources:
                try:
                    result = fn()
                    self.stats["total_records"] += result.get("count", 0)
                except Exception as e:
                    self._log(f"Failed: {name} - {e}")
                    traceback.print_exc()
                    self.stats["errors"].append(f"{name}: {e}")
        
        self.stats["end_time"] = datetime.now().isoformat()
        
        # Calculate totals
        self.stats["total_records"] = sum(
            s.get("count", 0) for s in self.stats["sources"].values()
        )
        
        # Save stats
        stats_path = self._save_json(self.stats, ".", "download_stats.json")
        
        # Print summary
        self._log("")
        self._log("="*60)
        self._log("DOWNLOAD COMPLETE")
        self._log("="*60)
        self._log(f"Total Records: {self.stats['total_records']:,}")
        self._log(f"Total Size: {self.stats['total_bytes'] / 1024 / 1024:.2f} MB")
        self._log(f"Errors: {len(self.stats['errors'])}")
        self._log("")
        
        for source, data in self.stats["sources"].items():
            self._log(f"  {source}: {data.get('count', 0):,} records")
        
        self._log("")
        self._log(f"Stats saved to: {stats_path}")
        
        return self.stats


def download_everything(output_dir: str = None, parallel: bool = False) -> Dict:
    """
    Convenience function to download all fungal data.
    
    Args:
        output_dir: Output directory (uses config default if None)
        parallel: Run downloads in parallel
    
    Returns:
        Download statistics
    """
    downloader = MasterDownloader(output_dir)
    return downloader.download_all(parallel=parallel)


if __name__ == "__main__":
    # Command line usage
    import argparse
    
    parser = argparse.ArgumentParser(description="Download all MINDEX fungal data")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output directory",
    )
    parser.add_argument(
        "--parallel", "-p",
        action="store_true",
        help="Run downloads in parallel",
    )
    
    args = parser.parse_args()
    
    stats = download_everything(output_dir=args.output, parallel=args.parallel)
    
    print(f"\nTotal: {stats['total_records']:,} records downloaded")
