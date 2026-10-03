"""Renew only local synthetic Brief10 browser fixtures; never persist private keys."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def renew(fixture_path: Path, mindex_root: Path, lifetime_hours: int) -> None:
    import sys

    sys.path.insert(0, str(mindex_root.resolve()))
    from mindex_api.ledger.provenance import (
        ProvenancePrincipal,
        RegisterRequest,
        canonical_record_bytes,
        source_signing_message,
    )

    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(hours=lifetime_hours)

    auth_private = ec.generate_private_key(ec.SECP256R1())
    auth_public = auth_private.public_key()
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(auth_public))
    kid = str(uuid.uuid4())
    fixtures["jwks"] = {"keys": [{**jwk, "kid": kid, "alg": "ES256", "use": "sig", "key_ops": ["verify"]}]}
    fixtures["created_at"] = now.isoformat().replace("+00:00", "Z")

    for user in fixtures["users"]:
        if user.get("label") not in {"User A", "User B"}:
            raise ValueError("Fixture must contain only the two expected synthetic identities")
        email = f"brief10-{user['label'].lower().replace(' ', '-')}+local-fixture@mycosoft.org"
        claims = {
            "iss": fixtures["issuer"],
            "aud": fixtures["audience"],
            "sub": user["subject"],
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "role": "authenticated",
            "is_anonymous": False,
            "email": email,
        }
        token = jwt.encode(claims, auth_private, algorithm="ES256", headers={"kid": kid})
        user["email"] = email
        user["access_token"] = token

        session = {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": lifetime_hours * 3600,
            "expires_at": int(expires.timestamp()),
            "refresh_token": secrets.token_urlsafe(32),
            "user": {"id": user["subject"], "aud": fixtures["audience"], "email": email},
        }
        cookie_payload = json.dumps(session, separators=(",", ":")).encode()
        user["cookie_value"] = "base64-" + base64.urlsafe_b64encode(cookie_payload).decode().rstrip("=")

        source_private = Ed25519PrivateKey.generate()
        public = source_private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        source = {
            "key_id": user["source_key_id"],
            "signed_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
            "signature_hex": "00" * 64,
        }
        registration = user["registration"]
        registration["idempotency_key"] = f"brief10-registration-{uuid.uuid4()}"
        registration["source"] = source
        registration["content_hash"] = "0" * 64
        principal = ProvenancePrincipal(fixtures["issuer"], user["subject"], user["tenant_id"], user["project_id"])
        parsed = RegisterRequest.model_validate(registration)
        registration["content_hash"] = hashlib.sha256(canonical_record_bytes(principal, parsed.evidence, parsed.source)).hexdigest()
        parsed = RegisterRequest.model_validate(registration)
        registration["source"]["signature_hex"] = source_private.sign(source_signing_message(principal, parsed.evidence, parsed.source)).hex()
        user["source_public_key_hex"] = public.hex()

    fixture_path.write_text(json.dumps(fixtures, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"identities_renewed": len(fixtures["users"]), "lifetime_hours": lifetime_hours,
                      "private_keys_persisted": False, "classification": "synthetic local fixture"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-file", required=True, type=Path)
    parser.add_argument("--mindex-root", required=True, type=Path)
    parser.add_argument("--lifetime-hours", type=int, choices=range(1, 13), default=8)
    args = parser.parse_args()
    renew(args.fixture_file, args.mindex_root, args.lifetime_hours)


if __name__ == "__main__":
    main()
