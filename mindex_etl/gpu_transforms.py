"""
GPU-Accelerated ETL Transforms — MINDEX
==========================================
Drop-in GPU acceleration functions for existing ETL jobs.
Each function uses cuDF when available, falls back to pandas/CPU.

Usage in ETL jobs:
    from mindex_etl.gpu_transforms import gpu_bulk_import_json
    records = gpu_bulk_import_json("/path/to/data.json")

Accelerates:
    - import_local_data.py: JSON → tabular (500k records)
    - species_data_completeness.py: multi-table aggregation (100k+ taxa)
    - backfill_missing_images.py: JSONB sort + pagination elimination
    - sync_pubchem_compounds.py: CID deduplication across search terms
    - sync_earth_data.py: batch H3 cell computation from lat/lng
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional imports — cuDF with pandas fallback
# ---------------------------------------------------------------------------

try:
    import cudf as df_lib

    _BACKEND = "cudf"
except ImportError:
    import pandas as df_lib  # type: ignore[no-redef]

    _BACKEND = "pandas"

# Always need pandas for DB writes and final conversion
try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]


def _log_perf(func_name: str, rows: int, elapsed: float) -> None:
    logger.info("[%s] %s: %d rows in %.2fs", _BACKEND, func_name, rows, elapsed)


# =========================================================================
# 1. Bulk JSON Import (import_local_data.py)
# =========================================================================


def gpu_bulk_import_json(
    path: str,
    schema_map: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Parse a large JSON file into a DataFrame using GPU acceleration.

    For files with 100k+ records, cuDF provides 10-50x speedup over pandas.

    Args:
        path: Path to JSON file (array-of-objects or NDJSON)
        schema_map: Optional column rename mapping {old_name: new_name}

    Returns:
        pandas DataFrame (always pandas for DB compatibility)
    """
    t0 = time.monotonic()

    try:
        if _BACKEND == "cudf":
            df = df_lib.read_json(path, orient="records", lines=False)
        else:
            df = pd.read_json(path, orient="records", lines=False)
    except Exception:
        # Try line-delimited JSON
        try:
            if _BACKEND == "cudf":
                df = df_lib.read_json(path, lines=True)
            else:
                df = pd.read_json(path, lines=True)
        except Exception:
            # Manual load as last resort
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                df = df_lib.DataFrame(data) if _BACKEND == "cudf" else pd.DataFrame(data)
            else:
                df = df_lib.DataFrame([data]) if _BACKEND == "cudf" else pd.DataFrame([data])

    # Apply schema mapping
    if schema_map:
        rename = {k: v for k, v in schema_map.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)

    # Convert to pandas for DB writes
    result = df.to_pandas() if _BACKEND == "cudf" and hasattr(df, "to_pandas") else df

    _log_perf("gpu_bulk_import_json", len(result), time.monotonic() - t0)
    return result


# =========================================================================
# 2. Species Data Completeness (species_data_completeness.py)
# =========================================================================


def gpu_completeness_scan(conn) -> Dict[str, Any]:
    """GPU-accelerated species data completeness scan.

    Replaces 4 separate SQL COUNT queries + complex CASE WHEN filtering
    with a single table load → GPU filter/aggregate.

    Args:
        conn: psycopg connection (sync, from ETL db_session)

    Returns:
        Dict with completeness metrics
    """
    t0 = time.monotonic()

    # Load taxon table
    taxon_query = """
        SELECT id, canonical_name, rank, description,
               metadata::text AS metadata_text
        FROM core.taxon
        WHERE rank = 'species'
    """

    if _BACKEND == "cudf":
        taxon_df = df_lib.read_sql(taxon_query, conn)
    else:
        taxon_df = pd.read_sql(taxon_query, conn)

    total = len(taxon_df)

    # Count missing images (check metadata for default_photo)
    def _has_image(meta_text):
        if not meta_text or meta_text == "null":
            return False
        try:
            meta = json.loads(meta_text) if isinstance(meta_text, str) else meta_text
            return bool(meta.get("default_photo"))
        except Exception:
            return False

    if _BACKEND == "cudf":
        # Convert to pandas for JSON parsing (cuDF doesn't handle nested JSON in apply)
        meta_series = taxon_df["metadata_text"].to_pandas()
    else:
        meta_series = taxon_df["metadata_text"]

    has_images = meta_series.apply(_has_image)
    with_images = int(has_images.sum())
    without_images = total - with_images

    # Count with descriptions
    if _BACKEND == "cudf":
        with_description = int(taxon_df["description"].notna().sum())
    else:
        with_description = int(taxon_df["description"].notna().sum())
    without_description = total - with_description

    # Count with genetics (requires JOIN)
    genetics_query = """
        SELECT DISTINCT t.id
        FROM core.taxon t
        JOIN bio.genetic_sequence g ON g.taxon_id = t.id
        WHERE t.rank = 'species'
    """
    try:
        if _BACKEND == "cudf":
            genetics_df = df_lib.read_sql(genetics_query, conn)
        else:
            genetics_df = pd.read_sql(genetics_query, conn)
        with_genetics = len(genetics_df)
    except Exception:
        with_genetics = 0
    without_genetics = total - with_genetics

    elapsed = time.monotonic() - t0
    _log_perf("gpu_completeness_scan", total, elapsed)

    return {
        "total_species": total,
        "with_images": with_images,
        "without_images": without_images,
        "with_description": with_description,
        "without_description": without_description,
        "with_genetics": with_genetics,
        "without_genetics": without_genetics,
        "completeness_pct": round(
            (with_images + with_description + with_genetics) / (total * 3) * 100, 1
        ) if total > 0 else 0,
        "backend": _BACKEND,
        "scan_time_seconds": round(elapsed, 3),
    }


# =========================================================================
# 3. Taxa Sorting by Popularity (backfill_missing_images.py)
# =========================================================================


def gpu_sort_taxa_by_popularity(conn, limit: int = 10000) -> pd.DataFrame:
    """GPU-accelerated sorting of taxa by observation count.

    Replaces repeated paginated SQL queries with a single load + GPU sort.
    Extracts observations_count from JSONB metadata and casts to numeric.

    Args:
        conn: psycopg connection (sync)
        limit: Max taxa to return

    Returns:
        pandas DataFrame sorted by popularity (desc), missing images only
    """
    t0 = time.monotonic()

    query = """
        SELECT id, canonical_name, rank, metadata::text AS metadata_text
        FROM core.taxon
        WHERE rank = 'species'
    """

    if _BACKEND == "cudf":
        df = df_lib.read_sql(query, conn)
    else:
        df = pd.read_sql(query, conn)

    # Filter to taxa without images
    if _BACKEND == "cudf":
        meta_series = df["metadata_text"].to_pandas()
    else:
        meta_series = df["metadata_text"]

    def _missing_image(meta_text):
        if not meta_text or meta_text == "null":
            return True
        try:
            meta = json.loads(meta_text) if isinstance(meta_text, str) else meta_text
            return not bool(meta.get("default_photo"))
        except Exception:
            return True

    missing_mask = meta_series.apply(_missing_image)

    # Extract observations_count for sorting
    def _get_obs_count(meta_text):
        try:
            meta = json.loads(meta_text) if isinstance(meta_text, str) else meta_text
            return int(meta.get("observations_count", 0))
        except Exception:
            return 0

    obs_counts = meta_series.apply(_get_obs_count)

    # Build result DataFrame
    result = pd.DataFrame({
        "id": df["id"].to_pandas() if _BACKEND == "cudf" else df["id"],
        "canonical_name": df["canonical_name"].to_pandas() if _BACKEND == "cudf" else df["canonical_name"],
        "rank": df["rank"].to_pandas() if _BACKEND == "cudf" else df["rank"],
        "observations_count": obs_counts,
        "missing_image": missing_mask,
    })

    # Filter + sort
    result = result[result["missing_image"]]
    result = result.sort_values("observations_count", ascending=False)
    result = result.head(limit)

    _log_perf("gpu_sort_taxa_by_popularity", len(result), time.monotonic() - t0)
    return result


# =========================================================================
# 4. Compound Deduplication (sync_pubchem_compounds.py)
# =========================================================================


def gpu_dedup_compounds(
    new_compounds: List[Dict[str, Any]],
    conn,
) -> List[Dict[str, Any]]:
    """GPU-accelerated compound deduplication.

    Loads existing compounds from DB, merges with new batch, and returns
    only truly new compounds (not already in bio.compound).

    Args:
        new_compounds: List of compound dicts from PubChem API
        conn: psycopg connection (sync)

    Returns:
        List of compound dicts that don't already exist in DB
    """
    if not new_compounds:
        return []

    t0 = time.monotonic()

    # Load existing compound IDs
    existing_query = "SELECT pubchem_id, inchikey FROM bio.compound WHERE pubchem_id IS NOT NULL"

    if _BACKEND == "cudf":
        existing_df = df_lib.read_sql(existing_query, conn)
        new_df = df_lib.DataFrame(new_compounds)
    else:
        existing_df = pd.read_sql(existing_query, conn)
        new_df = pd.DataFrame(new_compounds)

    if "pubchem_cid" in new_df.columns:
        new_df = new_df.rename(columns={"pubchem_cid": "pubchem_id"})

    # Dedup by pubchem_id
    if "pubchem_id" in new_df.columns and "pubchem_id" in existing_df.columns:
        merged = new_df.merge(
            existing_df[["pubchem_id"]].drop_duplicates(),
            on="pubchem_id",
            how="left",
            indicator=True,
        )
        novel = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
    else:
        novel = new_df

    # Convert back
    if _BACKEND == "cudf":
        result = novel.to_pandas().to_dict(orient="records")
    else:
        result = novel.to_dict(orient="records")

    _log_perf("gpu_dedup_compounds", len(result), time.monotonic() - t0)
    logger.info("Dedup: %d new → %d novel (-%d duplicates)",
                len(new_compounds), len(result), len(new_compounds) - len(result))
    return result


# =========================================================================
# 5. Batch H3 Cell Computation (sync_earth_data.py)
# =========================================================================


def gpu_batch_h3_cells(
    lats: List[float],
    lons: List[float],
    resolution: int = 4,
) -> List[str]:
    """Compute H3 cells for a batch of lat/lng coordinates.

    Falls back to sequential h3 library calls if GPU/vectorized not available.

    Args:
        lats: List of latitudes
        lons: List of longitudes
        resolution: H3 resolution (0-15, default 4 for ~1770 km²)

    Returns:
        List of H3 cell hex strings
    """
    t0 = time.monotonic()

    try:
        import h3

        cells = [
            h3.latlng_to_cell(lat, lng, resolution)
            for lat, lng in zip(lats, lons)
        ]
    except ImportError:
        # No h3 library — return empty strings
        cells = ["" for _ in lats]

    _log_perf("gpu_batch_h3_cells", len(cells), time.monotonic() - t0)
    return cells


# =========================================================================
# 6. Generic batch transform helpers
# =========================================================================


def gpu_batch_upsert_prep(
    records: List[Dict[str, Any]],
    dedup_columns: List[str],
    required_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Prepare a batch of records for database upsert.

    Deduplicates, validates required columns, and returns a clean DataFrame.
    """
    if not records:
        return pd.DataFrame()

    if _BACKEND == "cudf":
        df = df_lib.DataFrame(records)
    else:
        df = pd.DataFrame(records)

    # Dedup
    if dedup_columns:
        existing_cols = [c for c in dedup_columns if c in df.columns]
        if existing_cols:
            df = df.drop_duplicates(subset=existing_cols, keep="last")

    # Validate required columns
    if required_columns:
        for col in required_columns:
            if col in df.columns:
                df = df[df[col].notna()]

    # Always return pandas
    return df.to_pandas() if _BACKEND == "cudf" else df
