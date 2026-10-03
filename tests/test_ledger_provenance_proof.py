"""Independent offline vectors; these are not Bitcoin/Solana/Hypergraph consensus tests."""
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from mindex_api.ledger.provenance_proof import (
    FixtureProof, make_fixture_proof, public_commitment, verify_fixture,
)


def independent_vector():
    # This construction deliberately does not use the implementation's hash,
    # leaf, header, checkpoint or fixture factory helpers.
    digest = "1234567890abcdef" * 4
    transaction_id = "fixture:" + "ab" * 32
    leaf_bytes = ('{"chain":"solana","commitment":{"schema_version":'
                  '"mindex-public-commitment-v1","sha256":"' + digest +
                  '"},"transaction_id":"' + transaction_id + '"}').encode()
    leaf_hash = hashlib.sha256(b"\x00" + leaf_bytes).digest()
    left_sibling = hashlib.sha256(b"independent neighboring leaf").digest()
    merkle_root = hashlib.sha256(b"\x01" + left_sibling + leaf_hash).hexdigest()
    previous_hash = hashlib.sha256(b"MINDEX OFFLINE FIXTURE GENESIS V1").hexdigest()
    headers = []
    for height in range(1, 4):
        body = {"height": height, "parent_hash": previous_hash,
                "merkle_root": merkle_root, "nonce": "independent-vector"}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        previous_hash = hashlib.sha256(encoded).hexdigest()
        headers.append({**body, "block_hash": previous_hash})
    public_test_key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(
        b"MINDEX PUBLIC OFFLINE FIXTURE KEY - NOT SECRET").digest())
    signature = public_test_key.sign(
        f"mindex-offline-proof-v1:solana:3:{previous_hash}".encode()).hex()
    return FixtureProof(chain="solana", transaction_id=transaction_id, commitment=digest,
        inclusion_height=1, leaf_index=1, siblings=[left_sibling.hex()], headers=headers,
        tip_signature=signature)


def verify(proof, **kwargs):
    return verify_fixture(proof, commitment=proof.commitment, chain=proof.chain,
                          transaction_id=proof.transaction_id, **kwargs)


def test_independent_merkle_vector_finality_and_wrong_orientation():
    proof = independent_vector()
    result = verify(proof)
    assert result["state"] == "finalized" and result["confirmations"] == 3
    assert not result["onchain_confirmed"] and result["qualification"] == "offline_fixture"
    proof.leaf_index = 0
    with pytest.raises(ValueError, match="invalid_inclusion_proof"):
        verify(proof)


@pytest.mark.parametrize("mutation,error", [
    (lambda p: setattr(p.headers[1], "parent_hash", "f"*64), "invalid_header_continuity"),
    (lambda p: setattr(p.headers[0], "nonce", "tampered"), "invalid_header_hash"),
    (lambda p: setattr(p, "tip_signature", "0"*128), "invalid_checkpoint_signature"),
    (lambda p: setattr(p, "siblings", ["f"*64]), "invalid_inclusion_proof"),
    (lambda p: setattr(p, "siblings", ["zz"*32]), "invalid_merkle_sibling"),
    (lambda p: setattr(p, "leaf_index", 7), "invalid_merkle_index"),
    (lambda p: setattr(p, "inclusion_height", 4), "missing_inclusion_header"),
])
def test_tamper_rejected(mutation, error):
    proof = independent_vector()
    mutation(proof)
    with pytest.raises(ValueError, match=error):
        verify(proof)


@pytest.mark.parametrize("chain", ["bitcoin_op_return", "bitcoin_ordinals", "solana", "hypergraph"])
def test_chain_identity_not_interchangeable(chain):
    proof = make_fixture_proof("a"*64, chain=chain, depth=1)
    assert verify(proof)["state"] == "confirmed"
    different = "hypergraph" if chain != "hypergraph" else "solana"
    with pytest.raises(ValueError, match="proof_binding_mismatch"):
        verify_fixture(proof, commitment=proof.commitment, chain=different,
                       transaction_id=proof.transaction_id)


def test_payload_exact_allowlist_and_no_health_or_receipt_claims():
    assert public_commitment("a"*64) == {"schema_version": "mindex-public-commitment-v1", "sha256": "a"*64}
    with pytest.raises(ValueError):
        public_commitment("private-identifying-metadata")
    data = independent_vector().model_dump()
    for extra in ({"http_status": 200}, {"confirmed": True}, {"owner": "alice"},
                  {"qualification": "production"}, {"private_bytes": "telemetry"}):
        with pytest.raises(ValidationError):
            FixtureProof.model_validate({**data, **extra})


def test_bounded_proofs_and_reorg_require_valid_independent_checkpoint():
    original = independent_vector()
    old_block = original.headers[0].block_hash
    alternative = make_fixture_proof(original.commitment, "solana", depth=4, fork="other", included=False)
    # Fixture transaction identity is explicitly bound even for alternate canonical chains.
    alternative.transaction_id = original.transaction_id
    result = verify(alternative, previous_block_hash=old_block)
    assert result["state"] == "reorg" and result["confirmations"] == 0
    alternative.tip_signature = "f"*128
    with pytest.raises(ValueError, match="invalid_checkpoint_signature"):
        verify(alternative, previous_block_hash=old_block)
    with pytest.raises(ValueError, match="out_of_bounds"):
        make_fixture_proof("a"*64, depth=129)
    data = original.model_dump()
    data["siblings"] = ["a"*64]*33
    with pytest.raises(ValidationError):
        FixtureProof.model_validate(data)
