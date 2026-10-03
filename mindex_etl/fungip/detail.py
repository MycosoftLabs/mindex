"""Public detail projection; raw scientific claims remain auditable in storage."""
from uuid import UUID


def detail(row: dict, verification: dict | None = None, tokens: list | None = None) -> dict:
    record = row["record"]
    dna, image = record.get("dna") or {}, record.get("image")
    uuid = str(UUID(str(row["taxon_id"]))) if row.get("taxon_id") else None
    candidate = f"https://mycosoft.com/natureos/ancestry/species/{uuid}" if uuid else None
    verified = bool(verification and uuid and str(verification["taxon_id"]) == uuid and
                    verification["record_sha256"] == row["record_sha256"] and
                    verification["canonical_url"] == candidate and
                    all(verification["evidence"].get(k) is True for k in
                        ("name_checked", "taxonomy_checked", "dna_checked", "download_checked", "attribution_checked")))
    sequence_valid = row.get("sequence_valid") is True
    return {"species_id":row["species_id"], "mindex_uuid":uuid,
            "scientific_name":record["accepted_name"], "requested_name":record["requested_name"],
            "taxonomy":record.get("taxonomy",{}), "taxonomy_source":record.get("taxonomy_source"),
            "external_ids":row["external_ids"], "retrieved_at":record.get("retrieved_utc"),
            "canonical_url":candidate if verified else None, "candidate_uuid_url":candidate,
            "live_verified":verified, "missing_data_flags":row["missing_data_flags"],
            "validation_errors":row["validation_errors"], "record_sha256":row["record_sha256"],
            "image":image if row.get("image_valid") is True else None,
            "dna":{"accession_version":dna.get("accession_version"), "source_url":dna.get("source_url"),
                   "sequence":dna.get("sequence") if sequence_valid else None,
                   "sequence_scope":dna.get("sequence_scope"), "sha256":dna.get("sha256"),
                   "source_qualifiers":dna.get("source_qualifiers",{}),
                   "its_sequence":row.get("verified_its_sequence") if sequence_valid else None,
                   "its_span_1based_inclusive":dna.get("its_span_1based_inclusive") if row.get("verified_its_sequence") else None},
            "specimen_notice":"Photograph and DNA reference are linked by species concept; they are not established as the same specimen.",
            "tokens":[{key:token.get(key) for key in ("network","status","mint_address","transaction_signature","transaction_url","metadata_url","usepaid_url")} for token in (tokens or [])]}


def fasta(view: dict, kind: str) -> str:
    if kind not in ("reference", "its"):
        raise ValueError("Unknown sequence kind")
    sequence = view["dna"].get("sequence" if kind == "reference" else "its_sequence")
    if not sequence:
        raise ValueError("Validated sequence unavailable")
    header = f'>{view["species_id"]}|{view["dna"]["accession_version"]}|{kind}|{view["scientific_name"]}'
    return header + "\n" + "\n".join(sequence[i:i+80] for i in range(0,len(sequence),80)) + "\n"
