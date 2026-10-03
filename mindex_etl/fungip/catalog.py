"""Deterministic, offline validation and provenance preservation for FungiP 300."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from uuid import UUID
from urllib.parse import urlparse

IDENTIFIER_DERIVATION_VERSION = "fungip.identifiers.v2"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or unsafe package path: {name}")
    return path


def source_ids(record: dict) -> list[dict]:
    result = []
    for source in ("gbif", "col_xr"):
        provider = record.get(source) or {}
        response = provider.get("response") or {}
        usage = response.get("usage") or {}
        if usage.get("key") is not None:
            result.append({"source": source, "external_id": str(usage["key"]),
                           "source_url": provider.get("source_url"),
                           "retrieved_at": record.get("retrieved_utc"),
                           "rank": usage.get("rank"), "name": usage.get("canonicalName"),
                           "match_type": response.get("diagnostics", {}).get("matchType"),
                           "provider_release": None})
        # Preserve the matched usage (including synonyms) separately. Only the
        # captured, exact accepted species may add a second provider identity.
        accepted = response.get("acceptedUsage")
        diagnostics = response.get("diagnostics") or {}
        if isinstance(accepted, dict) and isinstance(diagnostics, dict):
            key = accepted.get("key")
            valid_key = (isinstance(key, str) and bool(key.strip()) and key == key.strip()) or (
                isinstance(key, int) and not isinstance(key, bool) and key > 0
            )
            if (valid_key and diagnostics.get("matchType") == "EXACT"
                    and accepted.get("rank") == "SPECIES"
                    and isinstance(record.get("accepted_name"), str)
                    and bool(record["accepted_name"].strip())
                    and accepted.get("canonicalName") == record["accepted_name"]
                    and not any(x["source"] == source and x["external_id"] == str(key) for x in result)):
                result.append({"source": source, "external_id": str(key),
                               "source_url": provider.get("source_url"),
                               "retrieved_at": record.get("retrieved_utc"),
                               "rank": accepted["rank"], "name": accepted["canonicalName"],
                               "match_type": diagnostics["matchType"], "provider_release": None,
                               "identifier_role": "acceptedUsage",
                               "matched_usage_external_id": str(usage["key"]) if usage.get("key") is not None else None})
    inat = record.get("inat") or {}
    if inat.get("id") is not None:
        result.append({"source": "inat", "external_id": str(inat["id"]),
                       "source_url": inat.get("source_url"), "retrieved_at": record.get("retrieved_utc"),
                       "rank": inat.get("rank"), "name": inat.get("name"),
                       "match_type": "candidate", "provider_release": None})
    return result


def identifier_manifest_sha256(entries: list[dict]) -> str:
    """Bind derived IDs to their unchanged scientific record hashes."""
    payload = [{"species_id": entry["species_id"], "record_sha256": entry["record_sha256"],
                "external_ids": entry["external_ids"]} for entry in entries]
    payload.sort(key=lambda entry: entry["species_id"])
    return digest(canonical(payload).encode("utf-8"))


def its_span(dna: dict) -> str | None:
    """Only exact, ordered ITS1/ITS2 feature bounds; no fuzzy/complement/join spans."""
    spans = {1: [], 2: []}
    for feature in dna.get("its_features", []):
        products = feature.get("qualifiers", {}).get("product", [])
        for n in (1, 2):
            if f"internal transcribed spacer {n}" in products:
                match = re.fullmatch(r"(\d+)\.\.(\d+)", feature.get("location", ""))
                if match is None:
                    return None
                spans[n].append(tuple(map(int, match.groups())))
    if len(spans[1]) != 1 or len(spans[2]) != 1:
        return None
    a, b = spans[1][0]
    c, d = spans[2][0]
    sequence = dna.get("sequence", "")
    if not 1 <= a <= b < c <= d <= len(sequence):
        return None
    if dna.get("its_span_1based_inclusive") is not None and dna["its_span_1based_inclusive"] != [a, d]:
        return None
    extracted = sequence[a - 1:d]
    return extracted if not dna.get("its_sequence") or dna["its_sequence"] == extracted else None


def validate_record(record: dict, root: Path) -> dict:
    errors, flags = [], list(record.get("launch_blockers", []))
    sid = record.get("species_id", "")
    if not re.fullmatch(r"FG\d{3}", sid):
        errors.append("invalid_species_id")
    if not record.get("accepted_name") or record.get("taxonomy", {}).get("KINGDOM") != "Fungi":
        errors.append("invalid_fungal_identity")
    ids = source_ids(record)
    for item in ids:
        if item["source"] != "inat" and (item["rank"] != "SPECIES" or item["match_type"] != "EXACT"):
            flags.append("taxon_curation_required")
    dna = record.get("dna") or {}
    sequence = dna.get("sequence", "")
    if sequence:
        if not re.fullmatch(r"[ACGTRYSWKMBDHVN]+", sequence):
            errors.append("invalid_sequence_alphabet")
        if not re.fullmatch(r"[A-Z]+_?\d+\.\d+", dna.get("accession_version", "")):
            errors.append("missing_accession_version")
        if len(sequence) != dna.get("length_bp") or digest(sequence.encode("ascii")) != dna.get("sha256"):
            errors.append("sequence_hash_or_length_mismatch")
    else:
        flags.append("reference_sequence_missing")
    extracted = its_span(dna)
    if dna.get("its_sequence") and extracted is None:
        flags.append("unsupported_its_extraction_quarantined")
    if extracted is None:
        flags.append("explicit_complete_its_unavailable")
    image = record.get("image")
    image_valid = bool(image)
    if image:
        try:
            original = local_file(root, image.get("local_path", ""))
            if file_hash(original) != image.get("sha256") or original.stat().st_size != image.get("bytes"):
                errors.append("image_hash_mismatch")
                image_valid = False
            from PIL import Image
            with Image.open(original) as pixels:
                if pixels.size != (image.get("width"), image.get("height")):
                    errors.append("image_dimensions_mismatch")
                    image_valid = False
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
            image_valid = False
        # Normalize only for validation; retain original precise license/credit text.
        license_code = re.sub(r"\s+", "-", image.get("license_code", "").lower().strip())
        if not (license_code in {"cc0", "cc-by", "cc-by-sa"} or
                re.fullmatch(r"cc-by(?:-sa)?-[1-4]\.0", license_code)):
            flags.append("image_license_requires_curation")
            image_valid = False
        if not image.get("attribution") or not image.get("source_page"):
            flags.append("image_attribution_incomplete")
            image_valid = False
        for url in (image.get("image_url"), image.get("source_page")):
            parsed = urlparse(url or "")
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                flags.append("image_url_invalid")
                image_valid = False
        short, long = sorted([image.get("width", 0), image.get("height", 0)])
        if short < 1080 or long < 1920:
            flags.append("image_hd_unqualified")
    else:
        flags.append("image_missing")
    for folder, expected in (("records", record), ("dna", dna)):
        try:
            observed = json.loads(local_file(root, f"data/{folder}/{sid}.json").read_text(encoding="utf-8"))
            if folder == "records":
                # Per-species research exports predate launch-field enrichment.
                differing = [k for k in observed if observed[k] != expected.get(k)]
                if differing:
                    flags.append("earlier_record_revision_differs:" + ",".join(sorted(differing)))
                if observed.get("species_id") != sid or observed.get("accepted_name") != record.get("accepted_name"):
                    errors.append("records_identity_conflict")
            elif observed != expected:
                errors.append(f"{folder}_duplicate_conflict")
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    flags.extend(["canonical_uuid_unresolved", "provider_release_unknown"])
    return {"species_id": sid, "record": record, "external_ids": ids,
            "record_sha256": digest(canonical(record).encode("utf-8")),
            "verified_its_sequence": extracted, "errors": sorted(set(errors)),
            "image_valid":image_valid, "sequence_valid":bool(sequence) and not any(e in errors for e in
                ("invalid_sequence_alphabet","sequence_hash_or_length_mismatch","missing_accession_version")),
            "missing_data_flags": sorted(set(flags)), "mindex_uuid": None,
            "canonical_url": None, "live_verified": False}


def audit(root: Path, duplicates_root: Path | None = None) -> dict:
    catalog_path = local_file(root, "data/catalog.json")
    records = json.loads(catalog_path.read_text(encoding="utf-8"))["species"]
    checked = [validate_record(r, root) for r in records]
    hashes, conflicts = [], []
    for line in local_file(root, "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split(None, 1)
        name = name.lstrip("*")
        try:
            actual = file_hash(local_file(root, name))
            status = "match" if actual == expected else "conflict"
        except ValueError:
            actual, status = None, "missing"
        hashes.append({"path": name, "expected": expected, "actual": actual, "status": status})
        if status != "match":
            conflicts.append(name)
    duplicates = []
    if duplicates_root:
        compare = [(name, name) for name in ("FungiP_300_Catalog.csv", "FungiP_300_Gallery.html", "LAUNCH_HANDOFF.md")]
        compare.append(("FungiP_300_Gallery-1.html", "FungiP_300_Gallery.html"))
        compare.extend(("FungiP_300_Data_and_Handoff/fungip_300/" + name, name) for name in
                       ("data/catalog.json", "FungiP_300_Catalog.csv", "FungiP_300_Gallery.html", "LAUNCH_HANDOFF.md", "MINDEX_HANDOFF.md"))
        for name, canonical_name in compare:
            candidate = duplicates_root / name
            if candidate.is_file():
                duplicates.append({"path": name, "canonical_sha256": file_hash(root / canonical_name),
                                   "top_level_sha256": file_hash(candidate),
                                   "identical": file_hash(root / canonical_name) == file_hash(candidate)})
    collisions = {}
    for field in ("species_id", "accepted_name"):
        collisions[field] = [v for v, n in Counter(r.get(field) for r in records).items() if n > 1]
    qualified_ids = Counter((x["source"], x["external_id"]) for r in checked for x in r["external_ids"])
    collisions["external_ids"] = [list(k) for k, n in qualified_ids.items() if n > 1]
    return {"schema_version": 1, "catalog_sha256": file_hash(catalog_path),
            "identifier_derivation": {"version": IDENTIFIER_DERIVATION_VERSION,
                                      "sha256": identifier_manifest_sha256(checked)},
            "records": checked, "hashes": hashes, "hash_conflicts": conflicts,
            "top_level_duplicates": duplicates, "duplicates": collisions,
            "counts": {"staged": len(checked), "production_imported": 0,
                       "sequences": sum(bool(r["record"].get("dna", {}).get("sequence")) for r in checked),
                       "images": sum(bool(r["record"].get("image")) for r in checked),
                       "supported_its": sum(r["verified_its_sequence"] is not None for r in checked),
                       "record_errors": sum(bool(r["errors"]) for r in checked)},
            "status": "STAGED_UNRESOLVED"}


def resolve(record: dict, candidates: list[dict]) -> tuple[str | None, str]:
    """Require source-qualified identity, species rank and accepted name agreement."""
    qualified = {(x["source"], x["external_id"]) for x in record["external_ids"]}
    matching = [x for x in candidates if (x["source"], str(x["external_id"])) in qualified]
    if not matching:
        return None, "unresolved"
    if any(x["rank"].lower() != "species" or x["canonical_name"] != record["record"]["accepted_name"] for x in matching):
        return None, "identity_conflict"
    values = {str(UUID(x["taxon_id"])) for x in matching}
    return (next(iter(values)), "resolved") if len(values) == 1 else (None, "source_conflict")
