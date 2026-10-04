"""Source-attested publication references parsed from exact scientific records."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any


def _text(parent: ET.Element, path: str) -> str | None:
    value = parent.findtext(path)
    normalized = " ".join((value or "").split())
    return normalized or None


def parse_genbank_publication_evidence(xml_content: str | bytes) -> list[dict[str, Any]]:
    """Parse reference candidates from one exact GenBank EFetch XML response.

    This records only references asserted by the source sequence record. It does
    not infer links from titles, and it deliberately excludes direct submissions.
    The returned items are evidence candidates, not reviewed publication links.
    """
    raw = xml_content if isinstance(xml_content, bytes) else xml_content.encode("utf-8")
    source_hash = hashlib.sha256(raw).hexdigest()
    root = ET.fromstring(raw)
    records = root.findall(".//GBSeq")
    if len(records) != 1:
        raise ValueError("exactly one GenBank record is required")
    record = records[0]
    accession_version = _text(record, "GBSeq_accession-version")
    organism = _text(record, "GBSeq_organism")
    if not accession_version or not re.fullmatch(r"[A-Za-z0-9_]+\.\d+", accession_version):
        raise ValueError("a versioned GenBank accession is required")

    taxon_ids: set[str] = set()
    for feature in record.findall(".//GBFeature"):
        if _text(feature, "GBFeature_key") != "source":
            continue
        for qualifier in feature.findall(".//GBQualifier"):
            if _text(qualifier, "GBQualifier_name") != "db_xref":
                continue
            value = _text(qualifier, "GBQualifier_value") or ""
            match = re.fullmatch(r"taxon:(\d+)", value)
            if match:
                taxon_ids.add(match.group(1))

    candidates: list[dict[str, Any]] = []
    for ordinal, reference in enumerate(record.findall("./GBSeq_references/GBReference"), start=1):
        title = _text(reference, "GBReference_title")
        journal = _text(reference, "GBReference_journal")
        if not title or title.casefold() in {"direct submission", "direct submission."}:
            continue
        reference_number = _text(reference, "GBReference_reference") or str(ordinal)
        authors = [
            " ".join((author.text or "").split())
            for author in reference.findall("./GBReference_authors/GBReference_author")
            if " ".join((author.text or "").split())
        ]
        pubmed_id = _text(reference, "GBReference_pubmed")
        doi = None
        for xref in reference.findall("./GBReference_xref/GBXref"):
            dbname = (_text(xref, "GBXref_dbname") or "").casefold()
            identifier = _text(xref, "GBXref_id")
            if dbname == "doi" and identifier:
                doi = identifier
            elif dbname == "pubmed" and identifier:
                pubmed_id = identifier

        normalized = {
            "accession_version": accession_version,
            "organism": organism,
            "source_taxon_ids": sorted(taxon_ids),
            "reference_number": reference_number,
            "title": title,
            "journal": journal,
            "authors": authors,
            "pubmed_id": pubmed_id,
            "doi": doi,
        }
        normalized_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        candidates.append({
            **normalized,
            "provider": "ncbi_genbank",
            "provider_source_record_id": f"{accession_version}#reference={reference_number}",
            "provider_source_url": f"https://www.ncbi.nlm.nih.gov/nuccore/{accession_version}",
            "association_method": "exact_genbank_taxon_record_reference",
            "evidence_state": "candidate_source_attested",
            "source_content_sha256": source_hash,
            "normalization_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
        })
    return candidates
