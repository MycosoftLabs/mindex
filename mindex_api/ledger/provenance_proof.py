"""Bounded, offline inclusion/finality fixtures, NEVER a chain consensus verifier.

This module has no network, wallet, signing/broadcast adapter, or production mode.
Its trust anchor is a public deterministic fixture authority. Its results MUST NOT
be represented as onchain confirmation. Bitcoin OP_RETURN and Ordinals retain
separate protocol identifiers; this fixture does not implement either protocol.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field

Chain = Literal["bitcoin_op_return", "bitcoin_ordinals", "solana", "hypergraph"]
Hash = str
FIXTURE_SEED = hashlib.sha256(b"MINDEX PUBLIC OFFLINE FIXTURE KEY - NOT SECRET").digest()
FIXTURE_PUBLIC_KEY = Ed25519PrivateKey.from_private_bytes(FIXTURE_SEED).public_key()
FIXTURE_GENESIS = hashlib.sha256(b"MINDEX OFFLINE FIXTURE GENESIS V1").hexdigest()
FINALITY_DEPTH = 3


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureHeader(StrictModel):
    height: int = Field(ge=1, le=128)
    parent_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    merkle_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(max_length=64)
    block_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FixtureProof(StrictModel):
    schema_version: Literal["mindex-offline-proof-v1"] = "mindex-offline-proof-v1"
    qualification: Literal["offline_fixture"] = "offline_fixture"
    chain: Chain
    transaction_id: str = Field(pattern=r"^fixture:[0-9a-f]{64}$")
    commitment: str = Field(pattern=r"^[0-9a-f]{64}$")
    inclusion_height: int = Field(ge=1, le=128)
    leaf_index: int = Field(ge=0, le=2**32 - 1)
    siblings: list[str] = Field(max_length=32)
    headers: list[FixtureHeader] = Field(min_length=1, max_length=128)
    tip_signature: str = Field(pattern=r"^[0-9a-f]{128}$")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_commitment(content_hash: str) -> dict[str, str]:
    """Explicit allowlist: no IDs, owner, telemetry, artifact paths or metadata."""
    if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
        raise ValueError("invalid_content_hash")
    return {"schema_version": "mindex-public-commitment-v1", "sha256": content_hash}


def _leaf(chain: str, transaction_id: str, commitment: str) -> str:
    payload = json.dumps({"chain": chain, "transaction_id": transaction_id,
                          "commitment": public_commitment(commitment)},
                         sort_keys=True, separators=(",", ":")).encode()
    return _hash(b"\x00" + payload)


def _header_hash(header: FixtureHeader) -> str:
    body = header.model_dump(exclude={"block_hash"})
    return _hash(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def _tip_message(chain: str, tip: FixtureHeader) -> bytes:
    return f"mindex-offline-proof-v1:{chain}:{tip.height}:{tip.block_hash}".encode()


def verify_fixture(proof: FixtureProof, *, commitment: str, chain: str,
                   transaction_id: str, previous_block_hash: str | None = None) -> dict:
    """Verify inclusion against a signed bounded fixture chain and detect forks.

    A reorg observation must provide a valid alternate canonical fixture chain;
    caller-provided status/finality strings are never accepted as evidence.
    """
    if proof.commitment != commitment or proof.chain != chain or proof.transaction_id != transaction_id:
        raise ValueError("proof_binding_mismatch")
    parent = FIXTURE_GENESIS
    for height, header in enumerate(proof.headers, 1):
        if header.height != height or header.parent_hash != parent:
            raise ValueError("invalid_header_continuity")
        if _header_hash(header) != header.block_hash:
            raise ValueError("invalid_header_hash")
        parent = header.block_hash
    try:
        FIXTURE_PUBLIC_KEY.verify(bytes.fromhex(proof.tip_signature),
                                  _tip_message(chain, proof.headers[-1]))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("invalid_checkpoint_signature") from exc
    if previous_block_hash and previous_block_hash not in {h.block_hash for h in proof.headers}:
        return {"state": "reorg", "qualification": "offline_fixture", "onchain_confirmed": False,
                "previous_block_hash": previous_block_hash, "tip_hash": parent,
                "tip_height": proof.headers[-1].height, "confirmations": 0}
    if proof.inclusion_height > len(proof.headers):
        raise ValueError("missing_inclusion_header")
    root = _leaf(chain, transaction_id, commitment)
    index = proof.leaf_index
    for sibling in proof.siblings:
        if len(sibling) != 64 or any(c not in "0123456789abcdef" for c in sibling):
            raise ValueError("invalid_merkle_sibling")
        pair = sibling + root if index & 1 else root + sibling
        root = _hash(b"\x01" + bytes.fromhex(pair))
        index >>= 1
    if index:
        raise ValueError("invalid_merkle_index")
    block = proof.headers[proof.inclusion_height - 1]
    if root != block.merkle_root:
        raise ValueError("invalid_inclusion_proof")
    depth = len(proof.headers) - proof.inclusion_height + 1
    return {"state": "finalized" if depth >= FINALITY_DEPTH else "confirmed",
            "qualification": "offline_fixture", "onchain_confirmed": False,
            "block_hash": block.block_hash, "tip_hash": parent, "tip_height": len(proof.headers),
            "confirmations": depth, "finality_threshold": FINALITY_DEPTH,
            "verifier": "mindex-offline-fixture-v1"}


def make_fixture_proof(commitment: str, chain: Chain = "bitcoin_op_return", *,
                       depth: int = 3, fork: str = "original", included: bool = True) -> FixtureProof:
    """Deterministic public test data, not a blockchain transaction or signature."""
    if not 1 <= depth <= 128:
        raise ValueError("fixture_depth_out_of_bounds")
    tx = "fixture:" + _hash(f"{chain}:{commitment}".encode())
    root = _leaf(chain, tx, commitment) if included else _hash(b"alternate transaction")
    headers = []
    parent = FIXTURE_GENESIS
    for height in range(1, depth + 1):
        header = FixtureHeader(height=height, parent_hash=parent, merkle_root=root,
                               nonce=f"{fork}:{height}", block_hash="0" * 64)
        header.block_hash = _header_hash(header)
        headers.append(header)
        parent = header.block_hash
    signature = Ed25519PrivateKey.from_private_bytes(FIXTURE_SEED).sign(
        _tip_message(chain, headers[-1])).hex()
    return FixtureProof(chain=chain, transaction_id=tx, commitment=commitment,
                        inclusion_height=1, leaf_index=0, siblings=[], headers=headers,
                        tip_signature=signature)
