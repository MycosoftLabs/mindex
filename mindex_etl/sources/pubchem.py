"""
PubChem Data Source
===================
Fetch fungal compound data from PubChem (NCBI).
https://pubchem.ncbi.nlm.nih.gov/

Focus on mycotoxins, fungal metabolites, and fungal-derived compounds.
"""
from __future__ import annotations

import time
from typing import Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

PUBCHEM_API = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUBCHEM_VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view"

# Search terms for fungal compounds
FUNGAL_SEARCH_TERMS = [
    "mycotoxin",
    "fungal metabolite", 
    "fungal compound",
    "mushroom toxin",
    "aflatoxin",
    "ochratoxin",
    "ergot alkaloid",
    "psilocybin",
    "amatoxin",
    "penicillin",
    "griseofulvin",
    "lovastatin",
    "cyclosporine",
    "cephalosporin",
    "fusarium toxin",
    "zearalenone",
    "fumonisin",
    "trichothecene",
    "citrinin",
    "patulin",
]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _search_compounds(
    client: httpx.Client,
    query: str,
    listkey_start: int = 0,
    listkey_count: int = 100,
) -> dict:
    """Search PubChem for compounds."""
    resp = client.get(
        f"{PUBCHEM_API}/compound/name/{query}/cids/JSON",
        params={
            "name_type": "word",
            "listkey_start": listkey_start,
            "listkey_count": listkey_count,
        },
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft; contact@mycosoft.org)"},
    )
    if resp.status_code == 404:
        return {"IdentifierList": {"CID": []}}
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _get_compound_properties(
    client: httpx.Client,
    cids: List[int],
) -> List[dict]:
    """Get compound properties from PubChem."""
    if not cids:
        return []
        
    cid_str = ",".join(str(cid) for cid in cids)
    properties = "MolecularFormula,MolecularWeight,IUPACName,CanonicalSMILES,InChI,InChIKey,XLogP,TPSA,Complexity,Charge"
    
    resp = client.get(
        f"{PUBCHEM_API}/compound/cid/{cid_str}/property/{properties}/JSON",
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft; contact@mycosoft.org)"},
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    
    data = resp.json()
    return data.get("PropertyTable", {}).get("Properties", [])


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _get_compound_synonyms(
    client: httpx.Client,
    cid: int,
) -> List[str]:
    """Get compound synonyms/names."""
    try:
        resp = client.get(
            f"{PUBCHEM_API}/compound/cid/{cid}/synonyms/JSON",
            timeout=30,
            headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft; contact@mycosoft.org)"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        info_list = data.get("InformationList", {}).get("Information", [])
        if info_list:
            return info_list[0].get("Synonym", [])[:20]  # Limit to 20 synonyms
    except Exception:
        pass
    return []


def map_pubchem_to_compound(props: dict, synonyms: List[str] = None) -> dict:
    """Map PubChem properties to MINDEX compound format."""
    return {
        "pubchem_cid": props.get("CID"),
        "name": props.get("IUPACName") or (synonyms[0] if synonyms else None),
        "molecular_formula": props.get("MolecularFormula"),
        "molecular_weight": props.get("MolecularWeight"),
        "canonical_smiles": props.get("CanonicalSMILES"),
        "inchi": props.get("InChI"),
        "inchi_key": props.get("InChIKey"),
        "xlogp": props.get("XLogP"),
        "tpsa": props.get("TPSA"),  # Topological Polar Surface Area
        "complexity": props.get("Complexity"),
        "charge": props.get("Charge"),
        "synonyms": synonyms or [],
        "source": "pubchem",
        "metadata": {
            "iupac_name": props.get("IUPACName"),
        },
    }


def iter_fungal_compounds(
    *,
    limit: int = 100,
    max_results: Optional[int] = None,
    delay_seconds: float = 0.5,
    include_synonyms: bool = False,
) -> Generator[Dict, None, None]:
    """Iterate through fungal compounds from PubChem."""
    
    seen_cids = set()
    total_yielded = 0
    
    with httpx.Client() as client:
        for search_term in FUNGAL_SEARCH_TERMS:
            if max_results and total_yielded >= max_results:
                break
                
            print(f"PubChem: Searching for '{search_term}'...")
            
            try:
                # Search for compound IDs
                result = _search_compounds(client, search_term, 0, limit)
                cids = result.get("IdentifierList", {}).get("CID", [])
                
                # Filter out already seen
                new_cids = [cid for cid in cids if cid not in seen_cids]
                seen_cids.update(new_cids)
                
                if not new_cids:
                    continue
                    
                # Get properties in batches
                batch_size = 50
                for i in range(0, len(new_cids), batch_size):
                    if max_results and total_yielded >= max_results:
                        break
                        
                    batch_cids = new_cids[i:i + batch_size]
                    props_list = _get_compound_properties(client, batch_cids)
                    
                    for props in props_list:
                        if max_results and total_yielded >= max_results:
                            break
                            
                        synonyms = []
                        if include_synonyms:
                            synonyms = _get_compound_synonyms(client, props.get("CID"))
                            time.sleep(0.2)  # Rate limit for synonyms
                            
                        yield map_pubchem_to_compound(props, synonyms)
                        total_yielded += 1
                        
                    time.sleep(delay_seconds)
                    
            except Exception as e:
                print(f"PubChem search error for '{search_term}': {e}")
                continue
                
            time.sleep(delay_seconds)


def iter_mycotoxins(
    *,
    limit: int = 100,
    max_results: Optional[int] = None,
    delay_seconds: float = 0.5,
) -> Generator[Dict, None, None]:
    """Iterate through known mycotoxins from PubChem."""
    
    # Specific mycotoxin names for more targeted search
    mycotoxins = [
        "aflatoxin B1", "aflatoxin B2", "aflatoxin G1", "aflatoxin G2",
        "ochratoxin A", "ochratoxin B",
        "deoxynivalenol", "nivalenol",
        "zearalenone", "fumonisin B1", "fumonisin B2",
        "patulin", "citrinin",
        "sterigmatocystin", "gliotoxin",
        "T-2 toxin", "HT-2 toxin",
        "diacetoxyscirpenol",
        "alternariol", "tenuazonic acid",
    ]
    
    total_yielded = 0
    
    with httpx.Client() as client:
        for toxin_name in mycotoxins:
            if max_results and total_yielded >= max_results:
                break
                
            try:
                result = _search_compounds(client, toxin_name, 0, 10)
                cids = result.get("IdentifierList", {}).get("CID", [])
                
                if cids:
                    props_list = _get_compound_properties(client, cids[:5])
                    
                    for props in props_list:
                        compound = map_pubchem_to_compound(props)
                        compound["compound_class"] = "mycotoxin"
                        compound["common_name"] = toxin_name
                        yield compound
                        total_yielded += 1
                        
                time.sleep(delay_seconds)
                
            except Exception as e:
                print(f"PubChem mycotoxin error for '{toxin_name}': {e}")
                continue


def count_fungal_compounds(search_term: str = "fungal") -> int:
    """Estimate count of fungal compounds in PubChem."""
    with httpx.Client() as client:
        try:
            result = _search_compounds(client, search_term, 0, 1)
            # PubChem doesn't return total count easily
            cids = result.get("IdentifierList", {}).get("CID", [])
            return len(cids)
        except Exception:
            return 0
