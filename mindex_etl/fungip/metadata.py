"""Read-only, bounded immutable species/metadata binding. Unsupported hosts stay unverified."""
from __future__ import annotations
import json
import re
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from .catalog import digest

HOSTS = {"ipfs.io", "gateway.pinata.cloud", "arweave.net"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Read-only evidence redirects are not allowed")


def read_uri_bytes(uri: str, maximum: int = 1024 * 1024) -> bytes:
    if uri.startswith("ipfs://"):
        tail = uri.removeprefix("ipfs://").removeprefix("ipfs/")
        if not re.fullmatch(r"(?:Qm[1-9A-HJ-NP-Za-km-z]{44}|bafy[a-z2-7]{20,})(?:/[-a-zA-Z0-9._]+)*",tail):
            raise ValueError("Invalid observed IPFS URI")
        uri = "https://ipfs.io/ipfs/" + tail
    parsed = urlparse(uri)
    if parsed.scheme != "https" or parsed.netloc not in HOSTS or parsed.username or parsed.password:
        raise ValueError("Metadata/image evidence host is unsupported; retain unknown outcome")
    with build_opener(NoRedirect()).open(Request(uri, headers={"Accept":"application/json,image/*"}),timeout=25) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Evidence body exceeds qualification bound")
    return data


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate metadata JSON key")
        result[key] = value
    return result


def validate_metadata(payload: dict, raw: bytes, image_raw: bytes) -> dict:
    value = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(value,dict) or value.get("name") != payload["name"] or value.get("symbol") != payload["symbol"]:
        raise ValueError("Metadata name/symbol differ from prepared species")
    website = value.get("external_url") or value.get("website") or (value.get("extensions") or {}).get("website")
    if website != payload["canonical_species_url"]:
        raise ValueError("Metadata lacks exact verified species URL association")
    if value.get("description") != payload["metadata_description"] or value.get("image") != payload["metadata_image_uri"]:
        raise ValueError("Metadata description/image URI differ from approved payload")
    if digest(image_raw) != payload["image_sha256"]:
        raise ValueError("Published image bytes differ from reviewed original")
    return {"species_id":payload["species_id"],"record_sha256":payload["record_sha256"],
            "metadata_uri":payload["metadata_uri"],"metadata_sha256":digest(raw),
            "metadata_name":value["name"],"metadata_symbol":value["symbol"],
            "canonical_species_url":website,"image_uri":value["image"],"image_sha256":digest(image_raw)}


def bind_metadata(payload: dict) -> dict:
    raw = read_uri_bytes(payload["metadata_uri"])
    image_raw = read_uri_bytes(payload["metadata_image_uri"], 32 * 1024 * 1024)
    return validate_metadata(payload, raw, image_raw)


def verify_binding(prepared: dict, raw: bytes, image_raw: bytes) -> None:
    binding = validate_metadata(prepared, raw, image_raw)
    if binding != prepared["metadata_binding"]:
        raise ValueError("Metadata changed since immutable preparation")
