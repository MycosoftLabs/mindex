"""
PubChem Compound Sync Job
=========================
Sync fungal compounds and mycotoxins from PubChem into MINDEX.
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

from ..db import db_session
from ..sources import pubchem


def _safe_compound_name(compound: dict) -> str:
    """
    `bio.compound.name` is NOT NULL.
    PubChem property payloads sometimes lack IUPACName and we don't always fetch synonyms
    (synonyms are extra requests). Fall back to a deterministic identifier so the record
    is still ingestible and can be backfilled later.
    """
    cid = compound.get("pubchem_cid")
    name = compound.get("name")
    if name and str(name).strip():
        return str(name).strip()
    synonyms = compound.get("synonyms") or []
    if synonyms:
        first = str(synonyms[0]).strip()
        if first:
            return first
    # Deterministic, derived from a real external ID (not mock data)
    return f"pubchem:{cid}" if cid else "pubchem:unknown"


def sync_pubchem_compounds(*, max_results: Optional[int] = None) -> int:
    """Sync fungal compounds from PubChem into MINDEX database."""
    inserted = 0
    updated = 0
    
    print(f"Starting PubChem compound sync (max_results={max_results})...")
    
    with db_session() as conn:
        for compound in pubchem.iter_fungal_compounds(limit=100, max_results=max_results, delay_seconds=0.5):
            cid = compound.get("pubchem_cid")
            if not cid:
                continue
            name = _safe_compound_name(compound)
            iupac_name = (compound.get("metadata") or {}).get("iupac_name")
                
            with conn.cursor() as cur:
                # Check if exists - using bio.compound schema
                cur.execute(
                    "SELECT id FROM bio.compound WHERE pubchem_id = %s LIMIT 1",
                    (cid,),
                )
                existing = cur.fetchone()
                
                if existing:
                    # Update existing
                    cur.execute(
                        """
                        UPDATE bio.compound SET
                            name = COALESCE(%s, name),
                            iupac_name = COALESCE(%s, iupac_name),
                            formula = %s,
                            molecular_weight = %s,
                            smiles = %s,
                            inchi = %s,
                            inchikey = %s,
                            metadata = %s::jsonb,
                            updated_at = now()
                        WHERE pubchem_id = %s
                        """,
                        (
                            name,
                            iupac_name,
                            compound.get("molecular_formula"),
                            compound.get("molecular_weight"),
                            compound.get("canonical_smiles"),
                            compound.get("inchi"),
                            compound.get("inchi_key"),
                            json.dumps({
                                "xlogp": compound.get("xlogp"),
                                "tpsa": compound.get("tpsa"),
                                "complexity": compound.get("complexity"),
                                "synonyms": compound.get("synonyms", []),
                            }),
                            cid,
                        ),
                    )
                    updated += 1
                else:
                    # Insert new - using bio.compound schema
                    cur.execute(
                        """
                        INSERT INTO bio.compound (
                            pubchem_id, name, formula, molecular_weight,
                            smiles, inchi, inchikey, iupac_name, source, metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            cid,
                            name,
                            compound.get("molecular_formula"),
                            compound.get("molecular_weight"),
                            compound.get("canonical_smiles"),
                            compound.get("inchi"),
                            compound.get("inchi_key"),
                            iupac_name,
                            "pubchem",
                            json.dumps({
                                "xlogp": compound.get("xlogp"),
                                "tpsa": compound.get("tpsa"),
                                "complexity": compound.get("complexity"),
                                "synonyms": compound.get("synonyms", []),
                            }),
                        ),
                    )
                    inserted += 1
                    
            total = inserted + updated
            if total and total % 100 == 0:
                conn.commit()
                print(f"PubChem: {inserted} inserted, {updated} updated...", flush=True)
                
    print(f"\nPubChem compound sync complete:")
    print(f"  Inserted: {inserted}")
    print(f"  Updated: {updated}")
    
    return inserted + updated


def sync_mycotoxins(*, max_results: Optional[int] = None) -> int:
    """Sync known mycotoxins from PubChem into MINDEX database."""
    inserted = 0
    
    print(f"Starting mycotoxin sync (max_results={max_results})...")
    
    with db_session() as conn:
        for compound in pubchem.iter_mycotoxins(limit=100, max_results=max_results, delay_seconds=0.5):
            cid = compound.get("pubchem_cid")
            if not cid:
                continue
                
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM bio.compound WHERE pubchem_id = %s LIMIT 1", (cid,))
                existing = cur.fetchone()

                name = _safe_compound_name(compound)
                metadata = json.dumps({
                    "common_name": compound.get("common_name"),
                    "xlogp": compound.get("xlogp"),
                    "tpsa": compound.get("tpsa"),
                })

                if existing:
                    cur.execute(
                        """
                        UPDATE bio.compound SET
                            name = COALESCE(%s, name),
                            formula = %s,
                            molecular_weight = %s,
                            smiles = %s,
                            inchi = %s,
                            inchikey = %s,
                            compound_type = COALESCE(%s, compound_type),
                            source = 'pubchem',
                            metadata = %s::jsonb,
                            updated_at = now()
                        WHERE pubchem_id = %s
                        """,
                        (
                            name,
                            compound.get("molecular_formula"),
                            compound.get("molecular_weight"),
                            compound.get("canonical_smiles"),
                            compound.get("inchi"),
                            compound.get("inchi_key"),
                            "mycotoxin",
                            metadata,
                            cid,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO bio.compound (
                            pubchem_id, name, formula, molecular_weight,
                            smiles, inchi, inchikey, compound_type, source, metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            cid,
                            name,
                            compound.get("molecular_formula"),
                            compound.get("molecular_weight"),
                            compound.get("canonical_smiles"),
                            compound.get("inchi"),
                            compound.get("inchi_key"),
                            "mycotoxin",
                            "pubchem",
                            metadata,
                        ),
                    )
                    inserted += 1
                    
            if inserted and inserted % 50 == 0:
                conn.commit()
                print(f"Mycotoxins: {inserted} synced...", flush=True)
                
    print(f"\nMycotoxin sync complete: {inserted} compounds")
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync PubChem fungal compounds")
    parser.add_argument("--max-results", type=int, default=None)
    parser.add_argument("--mycotoxins-only", action="store_true", help="Only sync mycotoxins")
    args = parser.parse_args()

    if args.mycotoxins_only:
        total = sync_mycotoxins(max_results=args.max_results)
    else:
        total = sync_pubchem_compounds(max_results=args.max_results)
        # Also sync mycotoxins
        total += sync_mycotoxins(max_results=args.max_results)
        
    print(f"Synced {total} PubChem records")


if __name__ == "__main__":
    main()
