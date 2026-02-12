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


# Known compound-species associations (curated list)
SPECIES_COMPOUNDS: Dict[str, List[str]] = {
    # Psilocybe species
    "Psilocybe cubensis": ["psilocybin", "psilocin", "baeocystin", "norbaeocystin"],
    "Psilocybe semilanceata": ["psilocybin", "psilocin", "baeocystin"],
    "Psilocybe azurescens": ["psilocybin", "psilocin", "baeocystin"],
    "Psilocybe cyanescens": ["psilocybin", "psilocin", "baeocystin"],
    
    # Lion's Mane
    "Hericium erinaceus": ["hericenone A", "hericenone B", "hericenone C", "erinacine A", "erinacine B"],
    
    # Reishi
    "Ganoderma lucidum": ["ganoderic acid A", "ganoderic acid B", "lucidenic acid", "beta-glucan"],
    
    # Cordyceps
    "Cordyceps militaris": ["cordycepin", "adenosine", "polysaccharides"],
    "Cordyceps sinensis": ["cordycepin", "adenosine"],
    
    # Turkey Tail
    "Trametes versicolor": ["polysaccharide-K", "polysaccharopeptide", "beta-glucan"],
    
    # Chaga
    "Inonotus obliquus": ["betulinic acid", "inotodiol", "melanin"],
    
    # Shiitake
    "Lentinula edodes": ["lentinan", "eritadenine", "beta-glucan"],
    
    # Maitake
    "Grifola frondosa": ["grifolan", "D-fraction", "beta-glucan"],
    
    # Amanita (toxic)
    "Amanita muscaria": ["muscimol", "ibotenic acid", "muscarine"],
    "Amanita phalloides": ["alpha-amanitin", "phalloidin"],
    
    # Oyster mushrooms
    "Pleurotus ostreatus": ["lovastatin", "ergothioneine", "pleuran"],
    
    # Other medicinal
    "Agaricus blazei": ["blazein", "beta-glucan"],
    "Grifola umbellata": ["polysaccharides", "ergosterol"],
}


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


def sync_compound_from_chemspider(
    client: ChemSpiderClient,
    conn,
    compound_name: str,
) -> Optional[UUID]:
    """
    Sync a single compound from ChemSpider.
    Returns the compound ID if successful.
    """
    # Check if already in database by name
    existing = find_compound_by_name(conn, compound_name)
    if existing and existing.get("chemspider_id"):
        return existing["id"]
    
    # Search in ChemSpider
    try:
        compound_data = client.search_fungal_compound(compound_name, save_locally=True)
        
        if not compound_data:
            print(f"  Compound '{compound_name}' not found in ChemSpider")
            return None
        
        # Insert or update in database
        compound_id = insert_compound(conn, compound_data)
        print(f"  Synced: {compound_name} (CS{compound_data.get('chemspider_id')})")
        
        return compound_id
        
    except ChemSpiderError as e:
        print(f"  ChemSpider error for '{compound_name}': {e}")
        return None
    except Exception as e:
        print(f"  Error syncing '{compound_name}': {e}")
        return None


def sync_species_compounds(
    client: ChemSpiderClient,
    conn,
    species_name: str,
    compounds: List[str],
) -> Tuple[int, int]:
    """
    Sync compounds for a species.
    Returns (compounds_synced, links_created).
    """
    # Find the taxon
    taxon = find_taxon_by_name(conn, species_name)
    if not taxon:
        print(f"  Species '{species_name}' not found in MINDEX")
        return 0, 0
    
    taxon_id = taxon["id"]
    compounds_synced = 0
    links_created = 0
    
    for compound_name in compounds:
        # Sync compound from ChemSpider
        compound_id = sync_compound_from_chemspider(client, conn, compound_name)
        
        if compound_id:
            compounds_synced += 1
            
            # Create link
            if link_taxon_compound(conn, taxon_id, compound_id):
                links_created += 1
    
    return compounds_synced, links_created


def run_full_sync(limit: Optional[int] = None) -> Dict:
    """
    Run a full sync of all known fungal compounds.
    """
    print("=" * 60)
    print("CHEMSPIDER FULL SYNC")
    print("=" * 60)
    
    stats = {
        "started_at": datetime.now().isoformat(),
        "species_processed": 0,
        "compounds_synced": 0,
        "links_created": 0,
        "errors": [],
    }
    
    # Check API key
    api_key = settings.chemspider_api_key or os.getenv("CHEMSPIDER_API_KEY")
    if not api_key:
        print("ERROR: CHEMSPIDER_API_KEY not configured")
        stats["errors"].append("CHEMSPIDER_API_KEY not configured")
        return stats
    
    with ChemSpiderClient(api_key) as client, get_db_connection() as conn:
        species_list = list(SPECIES_COMPOUNDS.items())
        if limit:
            species_list = species_list[:limit]
        
        for species_name, compounds in species_list:
            print(f"\nProcessing: {species_name}")
            print(f"  Compounds: {', '.join(compounds)}")
            
            try:
                synced, linked = sync_species_compounds(client, conn, species_name, compounds)
                stats["compounds_synced"] += synced
                stats["links_created"] += linked
                stats["species_processed"] += 1
                
            except Exception as e:
                error_msg = f"Error processing {species_name}: {e}"
                print(f"  {error_msg}")
                stats["errors"].append(error_msg)
            
            # Rate limiting
            time.sleep(1)
        
        conn.commit()
    
    stats["completed_at"] = datetime.now().isoformat()
    
    print("\n" + "=" * 60)
    print("SYNC COMPLETE")
    print(f"  Species processed: {stats['species_processed']}")
    print(f"  Compounds synced: {stats['compounds_synced']}")
    print(f"  Links created: {stats['links_created']}")
    print(f"  Errors: {len(stats['errors'])}")
    print("=" * 60)
    
    return stats


def run_incremental_sync() -> Dict:
    """
    Run an incremental sync (only new species without compounds).
    """
    print("=" * 60)
    print("CHEMSPIDER INCREMENTAL SYNC")
    print("=" * 60)
    
    stats = {
        "started_at": datetime.now().isoformat(),
        "species_checked": 0,
        "compounds_synced": 0,
        "links_created": 0,
    }
    
    api_key = settings.chemspider_api_key or os.getenv("CHEMSPIDER_API_KEY")
    if not api_key:
        print("ERROR: CHEMSPIDER_API_KEY not configured")
        return stats
    
    with ChemSpiderClient(api_key) as client, get_db_connection() as conn:
        # Find species without any compound links
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.canonical_name
                FROM core.taxon t
                WHERE t.rank = 'species'
                AND NOT EXISTS (
                    SELECT 1 FROM bio.taxon_compound tc WHERE tc.taxon_id = t.id
                )
                LIMIT 50
            """)
            species_without_compounds = cur.fetchall()
        
        for taxon in species_without_compounds:
            species_name = taxon["canonical_name"]
            
            # Check if we have known compounds for this species
            if species_name in SPECIES_COMPOUNDS:
                print(f"\nProcessing: {species_name}")
                compounds = SPECIES_COMPOUNDS[species_name]
                
                synced, linked = sync_species_compounds(client, conn, species_name, compounds)
                stats["compounds_synced"] += synced
                stats["links_created"] += linked
            
            stats["species_checked"] += 1
            time.sleep(0.5)
        
        conn.commit()
    
    stats["completed_at"] = datetime.now().isoformat()
    
    print("\n" + "=" * 60)
    print(f"Incremental sync complete: {stats['compounds_synced']} compounds synced")
    print("=" * 60)
    
    return stats


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
