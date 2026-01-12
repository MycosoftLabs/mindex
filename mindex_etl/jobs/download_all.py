"""
Master Download Job

Downloads ALL fungal data from ALL sources to local storage.
This is the primary data acquisition method for MINDEX.

Usage:
    python -m mindex_etl.jobs.download_all
    python -m mindex_etl.jobs.download_all --output /path/to/storage
    python -m mindex_etl.jobs.download_all --parallel
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from ..sources.master_downloader import MasterDownloader, download_everything


def main():
    parser = argparse.ArgumentParser(
        description="Download ALL MINDEX fungal data to local storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Download everything to default location:
    python -m mindex_etl.jobs.download_all

  Download to specific directory:
    python -m mindex_etl.jobs.download_all --output C:/MYCOSOFT/DATA

  Parallel downloads (faster):
    python -m mindex_etl.jobs.download_all --parallel

  Download specific source only:
    python -m mindex_etl.jobs.download_all --source inaturalist

Storage Requirements:
  - Minimum: 10 GB free space
  - Recommended: 50+ GB for full data with images
  - Available: 27TB (Dream Machine) + 16TB (NAS) + 12TB (local)
        """,
    )
    
    parser.add_argument(
        "--output", "-o",
        default="C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape",
        help="Output directory for downloaded data",
    )
    
    parser.add_argument(
        "--parallel", "-p",
        action="store_true",
        help="Run downloads in parallel (faster but more memory)",
    )
    
    parser.add_argument(
        "--source", "-s",
        choices=[
            "inaturalist", "mycobank", "gbif", "theyeasts",
            "fusarium", "mushroom_world", "index_fungorum", "all"
        ],
        default="all",
        help="Specific source to download (default: all)",
    )
    
    args = parser.parse_args()
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("="*60)
    print("MINDEX MASTER DATA DOWNLOAD")
    print("="*60)
    print(f"Output: {output_dir}")
    print(f"Parallel: {args.parallel}")
    print(f"Source: {args.source}")
    print(f"Started: {datetime.now().isoformat()}")
    print("="*60)
    print()
    
    downloader = MasterDownloader(str(output_dir))
    
    if args.source == "all":
        stats = downloader.download_all(parallel=args.parallel)
    else:
        # Download specific source
        source_methods = {
            "inaturalist": downloader.download_inaturalist,
            "mycobank": downloader.download_mycobank,
            "gbif": downloader.download_gbif,
            "theyeasts": downloader.download_theyeasts,
            "fusarium": downloader.download_fusarium,
            "mushroom_world": downloader.download_mushroom_world,
            "index_fungorum": downloader.download_index_fungorum,
        }
        
        method = source_methods.get(args.source)
        if method:
            result = method()
            stats = {
                "source": args.source,
                "count": result.get("count", 0),
                "filepath": result.get("filepath"),
            }
        else:
            print(f"Unknown source: {args.source}")
            sys.exit(1)
    
    print()
    print("="*60)
    print("DOWNLOAD COMPLETE")
    print("="*60)
    
    if args.source == "all":
        print(f"Total Records: {stats.get('total_records', 0):,}")
        print(f"Errors: {len(stats.get('errors', []))}")
    else:
        print(f"Records: {stats.get('count', 0):,}")
    
    print(f"Completed: {datetime.now().isoformat()}")
    print("="*60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
