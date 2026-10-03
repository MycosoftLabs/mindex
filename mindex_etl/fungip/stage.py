"""Emit batch drafts and per-record gaps from the audited canonical package; no DB or network."""
import argparse
import json
from pathlib import Path
from .catalog import audit
from .drafts import draft


def stage(package: Path, destination: Path, duplicates_root: Path | None = None):
    manifest = audit(package, duplicates_root)
    destination.mkdir(parents=True,exist_ok=True)
    drafts = [draft(entry) for entry in manifest["records"]]
    gaps = []
    for entry in manifest["records"]:
        r = entry["record"]; dna = r.get("dna") or {}
        its_reason = "exact_deposited_features" if entry["verified_its_sequence"] else (
            "claimed_span_unsupported_partial_or_note_only" if dna.get("its_sequence") else "no_unambiguous_ITS1_and_ITS2_feature_bounds")
        gaps.append({"species_id":entry["species_id"],"accepted_name":r["accepted_name"],
                     "requested_name":r["requested_name"],"identity_curation":r["requested_name"] != r["accepted_name"],
                     "reference_dna_valid":entry["sequence_valid"],"complete_its_supported":bool(entry["verified_its_sequence"]),
                     "its_reason":its_reason,"source_accession_version":dna.get("accession_version"),
                     "sequence_source_url":dna.get("source_url"),"deposited_features":dna.get("its_features",[]),
                     "image_present":bool(r.get("image")),"image_package_valid":entry["image_valid"],
                     "canonical_uuid":None,"flags":entry["missing_data_flags"],"errors":entry["errors"],
                     "remediation":"Review original accession feature annotations and taxonomic concept; add a versioned source-supported revision only. Do not infer complete ITS from primer names or whole-accession bounds." if not entry["verified_its_sequence"] else "Specialist review remains pending; extraction alone is not species identification certification."})
    for name,value in (("package-audit.json",manifest),("launch-drafts.json",drafts),("per-species-gaps.json",gaps)):
        (destination/name).write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding="utf-8")
    summary = {**manifest["counts"],"release_ready":0,"confirmed_tokens":0,"canonical_resolved":0,
               "metadata_binding_ready":0,"validator_version":"fungip.validation.v2"}
    (destination/"stage-counts.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Offline FungiP batch staging only")
    parser.add_argument("package",type=Path); parser.add_argument("destination",type=Path)
    parser.add_argument("--duplicates-root",type=Path)
    args=parser.parse_args()
    print(json.dumps(stage(args.package,args.destination,args.duplicates_root)))
