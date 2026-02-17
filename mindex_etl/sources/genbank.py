"""
GenBank (NCBI) Data Source
==========================
Fetch fungal genome/sequence data from NCBI GenBank/Nucleotide database.
https://www.ncbi.nlm.nih.gov/genbank/

Uses NCBI E-utilities API.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Fungi taxonomy ID in NCBI
FUNGI_TAXID = "4751"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _esearch(
    client: httpx.Client,
    db: str,
    term: str,
    retstart: int = 0,
    retmax: int = 100,
) -> dict:
    """Execute NCBI esearch query."""
    params = {
        "db": db,
        "term": term,
        "retstart": retstart,
        "retmax": retmax,
        "retmode": "json",
    }
    resp = client.get(
        f"{NCBI_BASE}/esearch.fcgi",
        params=params,
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft; contact@mycosoft.org)"},
    )
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _efetch(
    client: httpx.Client,
    db: str,
    ids: List[str],
    rettype: str = "gb",
    retmode: str = "xml",
) -> str:
    """Fetch records from NCBI."""
    params = {
        "db": db,
        "id": ",".join(ids),
        "rettype": rettype,
        "retmode": retmode,
    }
    resp = client.get(
        f"{NCBI_BASE}/efetch.fcgi",
        params=params,
        timeout=120,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft; contact@mycosoft.org)"},
    )
    resp.raise_for_status()
    return resp.text


def _parse_genbank_xml(xml_content: str) -> List[Dict]:
    """Parse GenBank XML into structured records."""
    records = []
    try:
        root = ET.fromstring(xml_content)
        for gbseq in root.findall(".//GBSeq"):
            seq_elem = gbseq.find("GBSeq_sequence")
            sequence = (seq_elem.text or "").replace(" ", "").replace("\n", "") if seq_elem is not None else ""
            record = {
                "accession": gbseq.findtext("GBSeq_primary-accession", ""),
                "locus": gbseq.findtext("GBSeq_locus", ""),
                "length": int(gbseq.findtext("GBSeq_length", "0") or "0"),
                "molecule_type": gbseq.findtext("GBSeq_moltype", ""),
                "definition": gbseq.findtext("GBSeq_definition", ""),
                "organism": gbseq.findtext("GBSeq_organism", ""),
                "taxonomy": gbseq.findtext("GBSeq_taxonomy", ""),
                "create_date": gbseq.findtext("GBSeq_create-date", ""),
                "update_date": gbseq.findtext("GBSeq_update-date", ""),
                "sequence_length": int(gbseq.findtext("GBSeq_length", "0") or "0"),
                "sequence": sequence,
            }
            
            # Get source features
            for feature in gbseq.findall(".//GBFeature"):
                if feature.findtext("GBFeature_key") == "source":
                    for qual in feature.findall(".//GBQualifier"):
                        qual_name = qual.findtext("GBQualifier_name", "")
                        qual_value = qual.findtext("GBQualifier_value", "")
                        if qual_name == "organism":
                            record["organism"] = qual_value
                        elif qual_name == "strain":
                            record["strain"] = qual_value
                        elif qual_name == "isolate":
                            record["isolate"] = qual_value
                        elif qual_name == "country":
                            record["country"] = qual_value
                        elif qual_name == "host":
                            record["host"] = qual_value
                        elif qual_name == "db_xref":
                            if "taxon:" in qual_value:
                                record["taxon_id"] = qual_value.replace("taxon:", "")
            
            records.append(record)
    except ET.ParseError as e:
        pass  # Skip malformed XML
        
    return records


def map_genbank_to_genome(record: dict) -> dict:
    """Map GenBank record to MINDEX genome format."""
    return {
        "accession": record.get("accession"),
        "source": "genbank",
        "organism": record.get("organism"),
        "taxon_id": record.get("taxon_id"),
        "strain": record.get("strain"),
        "sequence_length": record.get("sequence_length"),
        "molecule_type": record.get("molecule_type"),
        "definition": record.get("definition"),
        "country": record.get("country"),
        "host": record.get("host"),
        "sequence": record.get("sequence", ""),
        "metadata": {
            "locus": record.get("locus"),
            "create_date": record.get("create_date"),
            "update_date": record.get("update_date"),
            "taxonomy": record.get("taxonomy"),
        },
    }


def fetch_record_by_accession(accession: str) -> Optional[Dict]:
    """
    Fetch a single GenBank record by accession OR numeric GI/UID from NCBI.
    Returns full record including sequence, or None if not found.
    Used for on-demand ingest into MINDEX when a user requests detail.

    Handles both:
    - Text accessions: MK033196.1, AF123456, etc.  → search by [Accession] field
    - Numeric GI/UIDs: 1072298629, 123456789       → direct efetch by UID
    """
    if not accession or not accession.strip():
        return None
    accession = accession.strip()
    with httpx.Client() as client:
        if accession.isdigit():
            # Numeric GI/UID: fetch directly without esearch (no [Accession] query needed)
            time.sleep(0.34)  # NCBI rate limit
            xml_content = _efetch(client, "nucleotide", [accession], rettype="gb", retmode="xml")
            records = _parse_genbank_xml(xml_content)
            if not records:
                return None
            # Return whichever record NCBI gave us; don't enforce numeric equality
            return map_genbank_to_genome(records[0])
        else:
            # Text accession: search by [Accession] field, then fetch by UID
            result = _esearch(
                client,
                "nucleotide",
                f"{accession}[Accession]",
                retstart=0,
                retmax=1,
            )
            esearch_result = result.get("esearchresult", {})
            ids = esearch_result.get("idlist", [])
            if not ids:
                # Try without [Accession] field tag as last resort
                result = _esearch(client, "nucleotide", accession, retstart=0, retmax=1)
                ids = result.get("esearchresult", {}).get("idlist", [])
                if not ids:
                    return None
            time.sleep(0.34)  # NCBI rate limit
            xml_content = _efetch(client, "nucleotide", ids, rettype="gb", retmode="xml")
            records = _parse_genbank_xml(xml_content)
            if not records:
                return None
            return map_genbank_to_genome(records[0])


def iter_fungal_genomes(
    *,
    limit: int = 100,
    max_pages: Optional[int] = None,
    delay_seconds: float = 0.5,
) -> Generator[Dict, None, None]:
    """Iterate through fungal genome records from GenBank."""
    
    search_term = f"txid{FUNGI_TAXID}[Organism] AND biomol_genomic[PROP]"
    
    with httpx.Client() as client:
        # First get total count
        result = _esearch(client, "nucleotide", search_term, retstart=0, retmax=1)
        esearch_result = result.get("esearchresult", {})
        total_count = int(esearch_result.get("count", "0"))
        
        print(f"GenBank: Found {total_count:,} fungal genome records")
        
        offset = 0
        page = 1
        
        while True:
            if max_pages and page > max_pages:
                break
                
            # Search for IDs
            result = _esearch(client, "nucleotide", search_term, retstart=offset, retmax=limit)
            esearch_result = result.get("esearchresult", {})
            ids = esearch_result.get("idlist", [])
            
            if not ids:
                break
                
            # Fetch records
            try:
                xml_content = _efetch(client, "nucleotide", ids, rettype="gb", retmode="xml")
                records = _parse_genbank_xml(xml_content)
                
                for record in records:
                    yield map_genbank_to_genome(record)
                    
            except Exception as e:
                print(f"GenBank fetch error: {e}")
                
            page += 1
            offset += limit
            time.sleep(delay_seconds)  # NCBI rate limit: 3 requests/second without API key
            

def iter_fungal_sequences(
    gene: str = "ITS",
    *,
    limit: int = 100,
    max_pages: Optional[int] = None,
    delay_seconds: float = 0.5,
) -> Generator[Dict, None, None]:
    """Iterate through fungal sequences for a specific gene (e.g., ITS, LSU, SSU)."""
    
    # ITS = Internal Transcribed Spacer (standard fungal barcode)
    search_term = f"txid{FUNGI_TAXID}[Organism] AND {gene}[Gene]"
    
    with httpx.Client() as client:
        offset = 0
        page = 1
        
        while True:
            if max_pages and page > max_pages:
                break
                
            result = _esearch(client, "nucleotide", search_term, retstart=offset, retmax=limit)
            esearch_result = result.get("esearchresult", {})
            ids = esearch_result.get("idlist", [])
            
            if not ids:
                break
                
            try:
                xml_content = _efetch(client, "nucleotide", ids, rettype="gb", retmode="xml")
                records = _parse_genbank_xml(xml_content)
                
                for record in records:
                    mapped = map_genbank_to_genome(record)
                    mapped["gene"] = gene
                    yield mapped
                    
            except Exception as e:
                print(f"GenBank sequence fetch error: {e}")
                
            page += 1
            offset += limit
            time.sleep(delay_seconds)


def count_fungal_records(db: str = "nucleotide") -> int:
    """Count total fungal records in GenBank."""
    search_term = f"txid{FUNGI_TAXID}[Organism]"
    
    with httpx.Client() as client:
        result = _esearch(client, db, search_term, retstart=0, retmax=1)
        esearch_result = result.get("esearchresult", {})
        return int(esearch_result.get("count", "0"))
