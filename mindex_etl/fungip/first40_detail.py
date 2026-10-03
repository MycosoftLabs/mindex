"""Read-only public first40 projection; private source/operator fields are excluded."""
from __future__ import annotations

import json
import re
from .first40 import (
    FIRST40_IDS, LAUNCH_FIELDS, RECORD_FIELDS, REVIEW_FLAGS, VERIFICATION_BASIS,
    check_launch_fields, pacific_to_utc, utc_timestamp,
)

PUBLIC_TIMES = ("source_reported_as_of_pt", "source_reported_as_of_utc", "correction_recorded_at_utc")


def public_first40_launch(row: dict | None, parent: dict) -> dict | None:
    if row is None:
        return None
    if not isinstance(row,dict) or not isinstance(parent,dict) or not (RECORD_FIELDS|set(PUBLIC_TIMES)).issubset(row):
        raise ValueError("Incomplete first40 public record")
    # Only explicit normalized fields leave storage; no source paths, wallet,
    # financial cells, raw authority data or supposed receipt evidence.
    view = {key: row[key] for key in RECORD_FIELDS}
    view.update({key: row[key] for key in PUBLIC_TIMES})
    sid = view["species_id"]
    if sid not in FIRST40_IDS or sid != parent.get("species_id"):
        raise ValueError("First40 parent identity differs")
    parent_name = parent.get("accepted_name")
    if not isinstance(parent_name,str) or not parent_name.strip() or parent_name != view["accepted_name"]:
        raise ValueError("First40 top-level parent accepted name differs or is missing")
    record = parent.get("record")
    if isinstance(record,str):
        try: record = json.loads(record)
        except json.JSONDecodeError as exc: raise ValueError("Invalid first40 parent record") from exc
    if not isinstance(record,dict): raise ValueError("Invalid first40 parent record")
    dna,image = record.get("dna") or {},record.get("image") or {}
    if not isinstance(dna,dict) or not isinstance(image,dict): raise ValueError("Invalid first40 parent science/image")
    for source,value in (
        ("accepted_name",record.get("accepted_name")), ("ticker",record.get("ticker")),
        ("dna_sha256",dna.get("sha256")), ("dna_accession_version",dna.get("accession_version")),
        ("image_credit",image.get("attribution")), ("image_license",image.get("license_code")),
        ("image_sha256",image.get("sha256")),
    ):
        if view[source] != value:
            raise ValueError("First40 scientific/image parent disagreement")
    if parent.get("image_valid") is not True or parent.get("sequence_valid") is not True:
        raise ValueError("First40 parent science/image qualification unavailable")
    if any(not isinstance(view[key],str) or not re.fullmatch(r"[0-9a-f]{64}",view[key]) for key in ("dna_sha256","image_sha256")):
        raise ValueError("First40 scientific hash invalid")
    if (view["launch_status"] != "verified" or view["canonical_approved"] is not True or
        view["source_hash_match"] is not True or view["owner_entity"] != "MycoDAO" or
        view["verification_basis"] != VERIFICATION_BASIS or view["new_chain_verified"] is not False or
        view["candidate_launch"] is not None or not view["mint_address"] or view["recipient"] != "@nodefather"):
        raise ValueError("First40 source qualification differs")
    for key in ("catalog_review_flags","gap_flags","synonyms","superseded_mints"):
        if not isinstance(view[key],list) or any(not isinstance(v,str) for v in view[key]) or len(view[key]) != len(set(view[key])):
            raise ValueError("First40 public list invalid")
    if not set(REVIEW_FLAGS).issubset(view["catalog_review_flags"]):
        raise ValueError("First40 catalog reviews cleared")
    if view["synonyms"] != (["Ganoderma lingzhi"] if sid == "FG034" else []):
        raise ValueError("First40 synonym concept differs")
    if sid == "FG033" and view["accepted_name"] != "Ganoderma lucidum": raise ValueError("First40 FG033 concept differs")
    if sid == "FG034" and (view["accepted_name"] != "Ganoderma sichuanense" or "lingzhi_sichuanense_taxonomic_concept_review_open" not in view["catalog_review_flags"]):
        raise ValueError("First40 FG034 concept/review differs")
    fields = {key:view[key] for key in LAUNCH_FIELDS}
    check_launch_fields(fields,view["gap_flags"])
    if sid == "FG026":
        unsupplied = ("launch_tx","launched_at","launched_at_pt","solana_explorer_tx_url",
                      "solscan_tx_url","solscan_token_url","usepaid_short_url","token_program")
        if (any(fields[key] is not None or "blank_"+key not in view["gap_flags"] for key in unsupplied) or
            any(fields[key] is None for key in ("usepaid_url","pumpfun_url","solana_explorer_url","metadata_uri")) or
            fields["decimals"] != 6 or fields["supply_raw"] != "1000000000000000"):
            raise ValueError("First40 FG026 public inheritance guard")
    elif not fields["launch_tx"] or not fields["launched_at"]:
        raise ValueError("First40 original canonical observation incomplete")
    if view["mint_address"] in view["superseded_mints"]:
        raise ValueError("First40 superseded mint selected")
    # Database timestamptz values are serialized by the router before this check.
    if pacific_to_utc(view["source_reported_as_of_pt"]) != view["source_reported_as_of_utc"]:
        raise ValueError("First40 public source observation timezone differs")
    utc_timestamp(view["correction_recorded_at_utc"])
    return json.loads(json.dumps(view,ensure_ascii=False))
