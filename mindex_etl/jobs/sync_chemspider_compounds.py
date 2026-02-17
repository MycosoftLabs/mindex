"""
ChemSpider Compounds Sync Job

ETL job to sync fungal compound data from ChemSpider into MINDEX.
This runs periodically to enrich species with their known chemical compounds.

Usage:
    python -m mindex_etl.jobs.sync_chemspider_compounds [--full] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from ..config import settings
from ..sources.chemspider import (
    ChemSpiderClient,
    ChemSpiderError,
    download_fungal_compounds,
    iter_known_fungal_compounds,
    map_chemspider_compound,
)


DEFAULT_SEARCH_TERMS: List[str] = [
    # Broad terms / classes
    "mycotoxin",
    "fungal toxin",
    "ergot alkaloid",
    # High-signal known fungal metabolites / toxins (still used as SEARCH TERMS, not "seed data")
    "aflatoxin",
    "ochratoxin",
    "fumonisin",
    "zearalenone",
    "trichothecene",
    "patulin",
    "citrinin",
    "gliotoxin",
    "psilocybin",
    "psilocin",
    "muscimol",
    "ibotenic acid",
    "alpha-amanitin",
    "phalloidin",
    "cordycepin",
    "lovastatin",
    "cyclosporine",
    "penicillin",
    "griseofulvin",
]


def get_db_connection():
    """Get database connection."""
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def find_taxon_by_name(conn, canonical_name: str) -> Optional[Dict]:
    """Find a taxon by canonical name."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, canonical_name, common_name FROM core.taxon WHERE canonical_name ILIKE %s",
            (canonical_name,)
        )
        return cur.fetchone()


def find_compound_by_name(conn, name: str) -> Optional[Dict]:
    """Find a compound by name."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, chemspider_id FROM bio.compound WHERE name ILIKE %s",
            (name,)
        )
        return cur.fetchone()


def find_compound_by_chemspider_id(conn, chemspider_id: int) -> Optional[Dict]:
    """Find a compound by ChemSpider ID."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, chemspider_id FROM bio.compound WHERE chemspider_id = %s",
            (chemspider_id,)
        )
        return cur.fetchone()


def insert_compound(conn, compound_data: Dict) -> UUID:
    """Insert a new compound and return its ID."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO bio.compound (
                name, formula, molecular_weight, smiles, inchi, inchikey,
                chemspider_id, pubchem_id, source, metadata
            ) VALUES (
                %(name)s, %(formula)s, %(molecular_weight)s, %(smiles)s, %(inchi)s, %(inchikey)s,
                %(chemspider_id)s, %(pubchem_id)s, %(source)s, %(metadata)s::jsonb
            )
            ON CONFLICT (chemspider_id) DO UPDATE SET
                formula = EXCLUDED.formula,
                molecular_weight = EXCLUDED.molecular_weight,
                smiles = EXCLUDED.smiles,
                inchi = EXCLUDED.inchi,
                inchikey = EXCLUDED.inchikey,
                updated_at = now()
            RETURNING id
        """, {
            "name": compound_data.get("name"),
            "formula": compound_data.get("formula"),
            "molecular_weight": compound_data.get("molecular_weight"),
            "smiles": compound_data.get("smiles"),
            "inchi": compound_data.get("inchi"),
            "inchikey": compound_data.get("inchikey"),
            "chemspider_id": compound_data.get("chemspider_id"),
            "pubchem_id": compound_data.get("pubchem_id"),
            "source": compound_data.get("source", "chemspider"),
            "metadata": json.dumps(compound_data.get("metadata", {})),
        })
        result = cur.fetchone()
        return result["id"]


def link_taxon_compound(
    conn,
    taxon_id: UUID,
    compound_id: UUID,
    relationship_type: str = "produces",
    evidence_level: str = "reported",
    tissue_location: str = None,
) -> bool:
    """Create a link between a taxon and a compound."""
    with conn.cursor() as cur:
        try:
            cur.execute("""
                INSERT INTO bio.taxon_compound (
                    taxon_id, compound_id, relationship_type, evidence_level, tissue_location
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (taxon_id, compound_id, relationship_type) DO NOTHING
            """, (taxon_id, compound_id, relationship_type, evidence_level, tissue_location))
            return True
        except Exception as e:
            print(f"Failed to link taxon {taxon_id} to compound {compound_id}: {e}")
            return False


def sync_chemspider_compounds(
    *,
    max_results: Optional[int] = None,
    search_terms: Optional[List[str]] = None,
) -> int:
    """
    Ingest fungal-related compounds from ChemSpider into `bio.compound`.

    Important:
    - This job DOES NOT hardcode species→compound associations (no-mock-data policy).
    - It only ingests real compound records returned by the ChemSpider API.
    - Taxon linking should be done later using evidence-backed relationships (papers, annotations).
    """
    terms = search_terms or DEFAULT_SEARCH_TERMS
    api_key = settings.chemspider_api_key or os.getenv("CHEMSPIDER_API_KEY")
    if not api_key:
        raise ChemSpiderError("CHEMSPIDER_API_KEY not configured")

    synced = 0
    with ChemSpiderClient(api_key) as client, get_db_connection() as conn:
        for term in terms:
            if max_results and synced >= max_results:
                break
            try:
                # ChemSpider search-by-name supports broader matching than exact compound names.
                remaining = (max_results - synced) if max_results else 100
                results = client.search_by_name(term, max_results=min(remaining, 100), get_details=True)
                if not results:
                    continue

                for rec in results:
                    if max_results and synced >= max_results:
                        break
                    mapped = map_chemspider_compound(rec)
                    cs_id = mapped.get("chemspider_id")
                    if cs_id is None:
                        continue
                    existed = find_compound_by_chemspider_id(conn, int(cs_id))
                    insert_compound(conn, mapped)
                    synced += 1
                    if synced % 50 == 0:
                        print(f"ChemSpider: {synced} compounds synced...", flush=True)
                    # Lightweight rate limiting between inserts
                    time.sleep(0.05)

            except Exception as e:
                print(f"ChemSpider term '{term}' failed: {e}", flush=True)
                continue

        conn.commit()

    return synced


def run_full_sync(limit: Optional[int] = None) -> Dict:
    """
    Deprecated: previously used hardcoded species/compound maps.
    Use `sync_chemspider_compounds()` instead for API-driven ingest.
    """
    synced = sync_chemspider_compounds(max_results=limit)
    return {"started_at": datetime.now().isoformat(), "completed_at": datetime.now().isoformat(), "compounds_synced": synced}


def run_incremental_sync() -> Dict:
    """
    Run an incremental sync (API-driven compound ingest).
    """
    synced = sync_chemspider_compounds(max_results=200)
    return {"started_at": datetime.now().isoformat(), "completed_at": datetime.now().isoformat(), "compounds_synced": synced}


def main():
    parser = argparse.ArgumentParser(description="Sync fungal compounds from ChemSpider")
    parser.add_argument("--full", action="store_true", help="Run full sync of all known compounds")
    parser.add_argument("--limit", type=int, help="Limit number of species to process")
    parser.add_argument("--download-only", action="store_true", help="Only download compounds, don't sync to DB")
    
    args = parser.parse_args()
    
    if args.download_only:
        # Just download to local files
        download_fungal_compounds()
    elif args.full:
        run_full_sync(limit=args.limit)
    else:
        run_incremental_sync()


if __name__ == "__main__":
    main()
