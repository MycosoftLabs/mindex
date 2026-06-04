"""OP_RETURN helpers for Bitcoin hash commits (distinct from Ordinals inscriptions)."""
from __future__ import annotations

from .bitcoin_rpc_client import build_op_return_script


def op_return_from_content_hash(content_hash_hex: str) -> dict:
    h = content_hash_hex.lower().lstrip("0x")
    if len(h) != 64:
        raise ValueError("content_hash_hex must be 64 hex characters")
    prefix = b"MINDEX\x01"
    payload = prefix + bytes.fromhex(h)
    return build_op_return_script(payload)
