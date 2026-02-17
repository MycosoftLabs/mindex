"""
ChemSpider / RSC Compounds API Client

Integration with Royal Society of Chemistry's ChemSpider database
for chemical compound data (120M+ compounds).

API Base: https://api.rsc.org/compounds/v1
Docs: https://developer.rsc.org/api-reference

Provides:
- Compound search by name, formula, SMILES, InChI, mass
- Compound record retrieval with full details
- Molecular structure images
- External references (PubChem, CAS, etc.)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..config import settings


class ChemSpiderError(Exception):
    """Base exception for ChemSpider API errors."""
    pass


class ChemSpiderRateLimitError(ChemSpiderError):
    """Rate limit exceeded."""
    pass


class ChemSpiderAuthError(ChemSpiderError):
    """Authentication error."""
    pass


class ChemSpiderNotFoundError(ChemSpiderError):
    """Compound not found."""
    pass


def get_auth_headers() -> dict:
    """Get authentication headers for ChemSpider API."""
    api_key = settings.chemspider_api_key or os.getenv("CHEMSPIDER_API_KEY")
    
    if not api_key:
        raise ChemSpiderAuthError(
            "CHEMSPIDER_API_KEY not configured. "
            "Set it in environment or mindex_etl config."
        )
    
    return {
        "apikey": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)",
    }


def save_to_local(data: Any, filename: str, subdir: str = "chemspider") -> str:
    """Save data to local storage."""
    data_dir = Path(settings.local_data_dir) / subdir
    data_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = data_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    return str(filepath)


def map_chemspider_compound(record: dict) -> dict:
    """Map ChemSpider record to MINDEX compound format."""
    chemspider_id = record.get("id")
    name = record.get("commonName") or record.get("name")
    if not name:
        # `bio.compound.name` is NOT NULL; fall back to a deterministic external identifier.
        name = f"chemspider:{chemspider_id}" if chemspider_id is not None else "chemspider:unknown"
    return {
        "name": name,
        "formula": record.get("formula"),
        "molecular_weight": record.get("molecularWeight") or record.get("monoisotopicMass"),
        "smiles": record.get("smiles"),
        "inchi": record.get("inchi"),
        "inchikey": record.get("inchiKey"),
        "chemspider_id": chemspider_id,
        "source": "chemspider",
        "metadata": {
            "datasources_count": record.get("dataSourcesCount"),
            "references_count": record.get("referencesCount"),
            "pubmed_references_count": record.get("pubMedReferencesCount"),
            "rsc_references_count": record.get("rscReferencesCount"),
            "molecular_formula": record.get("formula"),
            "average_mass": record.get("molecularWeight"),
            "monoisotopic_mass": record.get("monoisotopicMass"),
            "nominal_mass": record.get("nominalMass"),
        },
    }


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=120),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _make_request(
    client: httpx.Client,
    method: str,
    endpoint: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> dict:
    """Make an authenticated request to ChemSpider API with retry logic."""
    url = f"{settings.chemspider_base_url}{endpoint}"
    headers = get_auth_headers()
    
    response = client.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=settings.http_timeout,
    )
    
    # Handle specific error codes
    if response.status_code == 401:
        raise ChemSpiderAuthError("Invalid API key")
    elif response.status_code == 403:
        raise ChemSpiderAuthError("API key lacks required permissions")
    elif response.status_code == 404:
        raise ChemSpiderNotFoundError("Resource not found")
    elif response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", 60))
        print(f"Rate limited, waiting {retry_after}s...", flush=True)
        time.sleep(retry_after)
        raise ChemSpiderRateLimitError(f"Rate limit exceeded, retry after {retry_after}s")
    
    response.raise_for_status()
    
    # Some endpoints return empty body
    if not response.content:
        return {}
    
    return response.json()


class ChemSpiderClient:
    """
    Client for ChemSpider/RSC Compounds API.
    
    Usage:
        client = ChemSpiderClient()
        
        # Search by name
        results = client.search_by_name("psilocybin")
        
        # Get compound details
        compound = client.get_compound(10086)
        
        # Search by formula
        results = client.search_by_formula("C12H17N2O4P")
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize ChemSpider client."""
        if api_key:
            os.environ["CHEMSPIDER_API_KEY"] = api_key
        self._client: Optional[httpx.Client] = None
        self._rate_limit_delay = settings.chemspider_rate_limit
    
    def __enter__(self):
        self._client = httpx.Client()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            self._client.close()
    
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client()
        return self._client
    
    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
    
    def _rate_limit(self):
        """Apply rate limiting delay."""
        time.sleep(self._rate_limit_delay)
    
    # =========================================================================
    # FILTERING ENDPOINTS (async search operations)
    # =========================================================================
    
    def filter_by_name(
        self,
        name: str,
        order_by: str = "recordId",
        order_direction: str = "ascending",
    ) -> str:
        """
        Start a filter-by-name query.
        
        Returns a queryId that can be used to check status and get results.
        """
        payload = {
            "name": name,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        
        result = _make_request(self.client, "POST", "/filter/name", json_body=payload)
        self._rate_limit()
        return result.get("queryId")
    
    def filter_by_formula(
        self,
        formula: str,
        order_by: str = "recordId",
        order_direction: str = "ascending",
    ) -> str:
        """Start a filter-by-formula query."""
        payload = {
            "formula": formula,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        
        result = _make_request(self.client, "POST", "/filter/formula", json_body=payload)
        self._rate_limit()
        return result.get("queryId")
    
    def filter_by_smiles(
        self,
        smiles: str,
        order_by: str = "recordId",
        order_direction: str = "ascending",
    ) -> str:
        """Start a filter-by-SMILES query."""
        payload = {
            "smiles": smiles,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        
        result = _make_request(self.client, "POST", "/filter/smiles", json_body=payload)
        self._rate_limit()
        return result.get("queryId")
    
    def filter_by_inchi(
        self,
        inchi: str,
        order_by: str = "recordId",
        order_direction: str = "ascending",
    ) -> str:
        """Start a filter-by-InChI query."""
        payload = {
            "inchi": inchi,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        
        result = _make_request(self.client, "POST", "/filter/inchi", json_body=payload)
        self._rate_limit()
        return result.get("queryId")
    
    def filter_by_inchikey(
        self,
        inchikey: str,
        order_by: str = "recordId",
        order_direction: str = "ascending",
    ) -> str:
        """Start a filter-by-InChIKey query."""
        payload = {
            "inchiKey": inchikey,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        
        result = _make_request(self.client, "POST", "/filter/inchikey", json_body=payload)
        self._rate_limit()
        return result.get("queryId")
    
    def filter_by_mass(
        self,
        mass: float,
        range_value: float = 0.5,
        order_by: str = "recordId",
        order_direction: str = "ascending",
    ) -> str:
        """Start a filter-by-mass query."""
        payload = {
            "mass": mass,
            "range": range_value,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        
        result = _make_request(self.client, "POST", "/filter/mass", json_body=payload)
        self._rate_limit()
        return result.get("queryId")
    
    def get_filter_status(self, query_id: str) -> dict:
        """Check the status of a filter query."""
        result = _make_request(self.client, "GET", f"/filter/{query_id}/status")
        return result
    
    def get_filter_results(
        self,
        query_id: str,
        start: int = 0,
        count: int = 100,
    ) -> List[int]:
        """Get results from a completed filter query (list of record IDs)."""
        params = {"start": start, "count": count}
        result = _make_request(self.client, "GET", f"/filter/{query_id}/results", params=params)
        return result.get("results", []) if isinstance(result, dict) else result
    
    def wait_for_filter_complete(
        self,
        query_id: str,
        timeout: int = 120,
        poll_interval: float = 2.0,
    ) -> dict:
        """Wait for a filter query to complete."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.get_filter_status(query_id)
            
            if status.get("status") == "Complete":
                return status
            elif status.get("status") == "Failed":
                raise ChemSpiderError(f"Query failed: {status.get('message')}")
            
            time.sleep(poll_interval)
        
        raise ChemSpiderError(f"Query timed out after {timeout}s")
    
    # =========================================================================
    # RECORD ENDPOINTS (get compound details)
    # =========================================================================
    
    def get_compound(self, record_id: int, fields: Optional[List[str]] = None) -> dict:
        """
        Get full compound record by ChemSpider ID.
        
        Args:
            record_id: ChemSpider record ID
            fields: Optional list of fields to return
        
        Returns:
            Compound record with all available data
        """
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        
        result = _make_request(self.client, "GET", f"/records/{record_id}/details", params=params)
        self._rate_limit()
        return result
    
    def get_compound_image(self, record_id: int) -> bytes:
        """Get compound structure image as PNG bytes."""
        url = f"{settings.chemspider_base_url}/records/{record_id}/image"
        headers = get_auth_headers()
        headers["Accept"] = "image/png"
        
        response = self.client.get(url, headers=headers, timeout=settings.http_timeout)
        response.raise_for_status()
        
        self._rate_limit()
        return response.content
    
    def get_compound_mol(self, record_id: int) -> str:
        """Get compound as MOL file format."""
        result = _make_request(self.client, "GET", f"/records/{record_id}/mol")
        self._rate_limit()
        return result.get("mol", "")
    
    def get_compound_external_refs(self, record_id: int) -> List[dict]:
        """Get external references for a compound (PubChem, CAS, etc.)."""
        result = _make_request(self.client, "GET", f"/records/{record_id}/externalreferences")
        self._rate_limit()
        return result.get("externalReferences", [])
    
    def get_batch_compounds(self, record_ids: List[int], fields: Optional[List[str]] = None) -> List[dict]:
        """Get multiple compounds in a single request."""
        payload = {"recordIds": record_ids}
        if fields:
            payload["fields"] = fields
        
        result = _make_request(self.client, "POST", "/records/batch", json_body=payload)
        self._rate_limit()
        return result.get("records", [])
    
    # =========================================================================
    # LOOKUP ENDPOINTS
    # =========================================================================
    
    def get_datasources(self) -> List[dict]:
        """Get list of all data sources in ChemSpider."""
        result = _make_request(self.client, "GET", "/lookups/datasources")
        return result.get("dataSources", [])
    
    # =========================================================================
    # TOOLS ENDPOINTS
    # =========================================================================
    
    def convert_structure(
        self,
        input_format: str,
        output_format: str,
        input_value: str,
    ) -> str:
        """
        Convert between chemical structure formats.
        
        Formats: SMILES, InChI, InChIKey, Mol
        """
        payload = {
            "input": input_value,
            "inputFormat": input_format,
            "outputFormat": output_format,
        }
        
        result = _make_request(self.client, "POST", "/tools/convert", json_body=payload)
        self._rate_limit()
        return result.get("output", "")
    
    def validate_inchikey(self, inchikey: str) -> bool:
        """Validate an InChIKey string."""
        payload = {"inchiKey": inchikey}
        result = _make_request(self.client, "POST", "/tools/validate/inchikey", json_body=payload)
        return result.get("valid", False)
    
    # =========================================================================
    # HIGH-LEVEL SEARCH METHODS
    # =========================================================================
    
    def search_by_name(
        self,
        name: str,
        max_results: int = 100,
        get_details: bool = True,
    ) -> List[dict]:
        """
        Search for compounds by name and return full results.
        
        Args:
            name: Compound name to search
            max_results: Maximum results to return
            get_details: Whether to fetch full details for each compound
        
        Returns:
            List of compound records
        """
        # Start the async query
        query_id = self.filter_by_name(name)
        
        # Wait for completion
        self.wait_for_filter_complete(query_id)
        
        # Get result IDs
        record_ids = self.get_filter_results(query_id, count=max_results)
        
        if not record_ids:
            return []
        
        if not get_details:
            return [{"chemspider_id": rid} for rid in record_ids]
        
        # Fetch full details in batches
        compounds = []
        batch_size = 50
        
        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i:i + batch_size]
            batch_results = self.get_batch_compounds(batch)
            compounds.extend(batch_results)
        
        return compounds
    
    def search_by_formula(
        self,
        formula: str,
        max_results: int = 100,
        get_details: bool = True,
    ) -> List[dict]:
        """Search for compounds by molecular formula."""
        query_id = self.filter_by_formula(formula)
        self.wait_for_filter_complete(query_id)
        record_ids = self.get_filter_results(query_id, count=max_results)
        
        if not record_ids or not get_details:
            return [{"chemspider_id": rid} for rid in record_ids] if record_ids else []
        
        compounds = []
        for i in range(0, len(record_ids), 50):
            batch = record_ids[i:i + 50]
            compounds.extend(self.get_batch_compounds(batch))
        
        return compounds
    
    def search_fungal_compound(
        self,
        name: str,
        save_locally: bool = True,
    ) -> Optional[dict]:
        """
        Search for a fungal compound and return mapped result.
        
        This is the main entry point for MINDEX integration.
        """
        try:
            results = self.search_by_name(name, max_results=5)
            
            if not results:
                return None
            
            # Take the first (most relevant) result
            compound = results[0]
            mapped = map_chemspider_compound(compound)
            
            # Get external references
            if mapped.get("chemspider_id"):
                try:
                    refs = self.get_compound_external_refs(mapped["chemspider_id"])
                    mapped["metadata"]["external_references"] = refs
                    
                    # Extract PubChem ID if available
                    for ref in refs:
                        if ref.get("sourceName", "").lower() == "pubchem":
                            mapped["pubchem_id"] = ref.get("externalId")
                            break
                except Exception:
                    pass  # External refs are optional
            
            if save_locally:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"compound_{name.replace(' ', '_')}_{timestamp}.json"
                save_to_local(mapped, filename)
            
            return mapped
            
        except ChemSpiderNotFoundError:
            return None
        except Exception as e:
            print(f"Error searching for compound '{name}': {e}", flush=True)
            return None


def iter_known_fungal_compounds(
    client: Optional[ChemSpiderClient] = None,
) -> Generator[dict, None, None]:
    """
    Iterate over known fungal compounds.
    
    This uses a curated list of compounds commonly found in fungi.
    """
    close_client = False
    if client is None:
        client = ChemSpiderClient()
        close_client = True
    
    # Curated list of important fungal compounds
    FUNGAL_COMPOUNDS = [
        # Psilocybe compounds
        "psilocybin",
        "psilocin",
        "baeocystin",
        "norbaeocystin",
        # Lion's Mane compounds
        "hericenone A",
        "hericenone B",
        "erinacine A",
        "erinacine B",
        # Reishi compounds
        "ganoderic acid A",
        "ganoderic acid B",
        "lucidenic acid",
        # Cordyceps compounds
        "cordycepin",
        "adenosine",
        # Turkey Tail compounds
        "polysaccharide-K",
        "polysaccharopeptide",
        # Chaga compounds
        "betulinic acid",
        "inotodiol",
        # Shiitake compounds
        "lentinan",
        "eritadenine",
        # Maitake compounds
        "grifolan",
        "D-fraction",
        # General fungal compounds
        "ergosterol",
        "chitin",
        "beta-glucan",
        "lovastatin",
        "muscimol",
        "ibotenic acid",
        "muscarine",
        "amatoxin",
        "phalloidin",
        "coprine",
        "orellanine",
        "gyromitrin",
    ]
    
    try:
        for compound_name in FUNGAL_COMPOUNDS:
            print(f"Searching for: {compound_name}", flush=True)
            
            try:
                compound = client.search_fungal_compound(compound_name, save_locally=True)
                
                if compound:
                    yield compound
                else:
                    print(f"  Not found in ChemSpider", flush=True)
                    
            except Exception as e:
                print(f"  Error: {e}", flush=True)
                continue
    finally:
        if close_client:
            client.close()


def download_fungal_compounds(output_dir: str = None) -> str:
    """
    Download all known fungal compounds from ChemSpider.
    
    Returns path to saved JSON file.
    """
    output_dir = output_dir or settings.local_data_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    all_compounds = []
    
    print("=" * 60)
    print("DOWNLOADING FUNGAL COMPOUNDS FROM CHEMSPIDER")
    print("=" * 60)
    
    with ChemSpiderClient() as client:
        for compound in iter_known_fungal_compounds(client):
            all_compounds.append(compound)
            print(f"  Found: {compound.get('name')} (CS{compound.get('chemspider_id')})", flush=True)
    
    # Save complete dump
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fungal_compounds_complete_{timestamp}.json"
    filepath = Path(output_dir) / "chemspider" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "downloaded_at": datetime.now().isoformat(),
            "total_compounds": len(all_compounds),
            "compounds": all_compounds,
        }, f, indent=2, ensure_ascii=False)
    
    print("=" * 60)
    print(f"COMPLETE: Downloaded {len(all_compounds)} compounds")
    print(f"Saved to: {filepath}")
    print("=" * 60)
    
    return str(filepath)


# Convenience function for quick lookups
def lookup_compound(name: str, api_key: Optional[str] = None) -> Optional[dict]:
    """
    Quick lookup of a single compound by name.
    
    Usage:
        compound = lookup_compound("psilocybin")
        print(compound["formula"])  # C12H17N2O4P
    """
    with ChemSpiderClient(api_key) as client:
        return client.search_fungal_compound(name, save_locally=False)
