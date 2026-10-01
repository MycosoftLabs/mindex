"""Offline boundary tests use real RSA/EC signatures, never live identities."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from copy import deepcopy
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from mindex_api.retention.contracts import Principal, RetentionError
from mindex_api.retention.identity import IdentityVerifier, MAX_JWKS_BYTES, MAX_TOKEN_BYTES

ISSUER = "https://retention-fixture.supabase.co/auth/v1"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
USER = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
OTHER_PROJECT = "44444444-4444-4444-8444-444444444444"


@pytest.fixture(scope="module")
def signing_keys():
    return {
        "rsa-a": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "rsa-b": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "ec-a": ec.generate_private_key(ec.SECP256R1()),
    }


def public_jwk(key, kid):
    if isinstance(key, rsa.RSAPrivateKey):
        value = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        alg = "RS256"
    else:
        value = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
        alg = "ES256"
    return {**value, "kid": kid, "alg": alg, "use": "sig", "key_ops": ["verify"]}


def claims(**overrides):
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": USER,
        "iat": now - 5,
        "exp": now + 300,
        "role": "authenticated",
        "is_anonymous": False,
        **overrides,
    }


def token(key, kid="rsa-a", *, payload=None, headers=None):
    algorithm = "RS256" if isinstance(key, rsa.RSAPrivateKey) else "ES256"
    return jwt.encode(
        claims() if payload is None else payload,
        key,
        algorithm=algorithm,
        headers={"kid": kid, **(headers or {})},
    )


def raw_rsa_token(key, header, payload):
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    message = encode(header) + b"." + encode(payload)
    signature = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256).sign(message, key)
    return (message + b"." + encode(signature)).decode("ascii")


class Provider:
    def __init__(self, keys):
        self.value = {"keys": keys}
        self.calls = 0
        self.error = None

    async def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return deepcopy(self.value)


def verifier(provider, **kwargs):
    return IdentityVerifier(
        issuer=ISSUER,
        audience="authenticated",
        key_provider=provider,
        enabled=True,
        clock_skew_seconds=0,
        **kwargs,
    )


async def denied(verifier_, value, *, status=401, tenant=TENANT, project=PROJECT):
    with pytest.raises(RetentionError) as failure:
        await verifier_.verify(value, tenant, project)
    assert failure.value.status == status
    assert str(failure.value) in {"unauthorized", "identity_unavailable", "invalid_scope"}
    # No bearer contents or detailed provider exceptions enter public errors.
    assert "eyJ" not in str(failure.value)


@pytest.mark.parametrize("kid", ["rsa-a", "ec-a"])
async def test_valid_signed_supabase_user(signing_keys, kid):
    key = signing_keys[kid]
    source = Provider([public_jwk(key, kid)])
    value = token(key, kid)
    actual = await verifier(source).verify(value, TENANT, PROJECT)
    assert actual == Principal(ISSUER, USER, TENANT, PROJECT)
    assert source.calls == 1


async def test_selected_scope_is_not_token_metadata_or_membership(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    value = token(key, payload=claims(tenant_id="attacker", project_id="attacker", user_metadata={"owner": True}))
    verifier_ = verifier(source)
    first = await verifier_.verify(value, TENANT, PROJECT)
    second = await verifier_.verify(value, UUID(TENANT), UUID(OTHER_PROJECT))
    assert first.subject == second.subject == USER
    assert first.project_id == PROJECT
    assert second.project_id == OTHER_PROJECT
    assert first.tenant_id == second.tenant_id == TENANT
    assert not hasattr(first, "membership")
    assert source.calls == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example/auth/v1"},
        {"aud": "service_role"},
        {"aud": ["authenticated", "unrelated"]},
        {"exp": 1},
        {"iat": 9_999_999_999},
        {"nbf": 9_999_999_999},
        {"exp": True},
        {"iat": "1"},
        {"nbf": "1"},
        {"iat": 1.5},
        {"exp": float("nan")},
        {"sub": "not-a-uuid"},
        {"sub": "00000000-0000-0000-0000-000000000000"},
        {"role": "anon"},
        {"role": "service_role"},
        {"role": "operator"},
        {"role": ["authenticated"]},
        {"is_anonymous": True},
        {"is_anonymous": "false"},
        {"is_anonymous": 0},
    ],
)
async def test_rejects_signed_invalid_claims(signing_keys, overrides):
    key = signing_keys["rsa-a"]
    await denied(verifier(Provider([public_jwk(key, "rsa-a")])), token(key, payload=claims(**overrides)))


@pytest.mark.parametrize("missing", ["iss", "aud", "sub", "exp", "iat", "role", "is_anonymous"])
async def test_required_claims_cannot_be_omitted(signing_keys, missing):
    key = signing_keys["rsa-a"]
    payload = claims()
    del payload[missing]
    await denied(verifier(Provider([public_jwk(key, "rsa-a")])), token(key, payload=payload))


async def test_nbf_optional_but_validated_when_present(signing_keys):
    key = signing_keys["rsa-a"]
    verifier_ = verifier(Provider([public_jwk(key, "rsa-a")]))
    assert await verifier_.verify(token(key), TENANT, PROJECT)
    assert await verifier_.verify(token(key, payload=claims(nbf=int(time.time()) - 1)), TENANT, PROJECT)


async def test_impossible_lifetime_rejected_even_with_clock_leeway(signing_keys):
    key = signing_keys["rsa-a"]
    now = int(time.time())
    verifier_ = IdentityVerifier(
        issuer=ISSUER, audience="authenticated", enabled=True,
        key_provider=Provider([public_jwk(key, "rsa-a")]), clock_skew_seconds=60,
    )
    await denied(verifier_, token(key, payload=claims(iat=now, exp=now)))


@pytest.mark.parametrize("algorithm", ["HS256", "none", "RS512"])
async def test_unapproved_algorithms_rejected_before_key_fetch(signing_keys, algorithm):
    key = signing_keys["rsa-a"]
    signing_key = "fixture-secret-of-at-least-thirty-two-bytes" if algorithm == "HS256" else key
    if algorithm == "none":
        signing_key = None
    value = jwt.encode(claims(), signing_key, algorithm=algorithm, headers={"kid": "rsa-a"})
    source = Provider([public_jwk(key, "rsa-a")])
    await denied(verifier(source), value)
    assert source.calls == 0


async def test_attacker_signature_and_forged_payload_rejected(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    verifier_ = verifier(source)
    await denied(verifier_, token(signing_keys["rsa-b"], "rsa-a"))
    original = token(key)
    head, _, signature = original.split(".")
    forged = base64.urlsafe_b64encode(json.dumps(claims(sub=OTHER_PROJECT)).encode()).decode().rstrip("=")
    await denied(verifier_, f"{head}.{forged}.{signature}")
    assert source.calls == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"jku": "https://attacker.example/jwks.json"},
        {"jwk": {"kty": "oct", "k": "attacker"}},
        {"x5u": "https://attacker.example/cert"},
        {"x5c": ["attacker"]},
        {"crit": ["custom"]},
        {"b64": True},
        {"kid": "../some/key"},
        {"kid": "k" * 129},
        {"kid": ""},
        {"typ": "ID_TOKEN"},
    ],
)
async def test_token_headers_cannot_select_key_sources(signing_keys, extra):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    value = raw_rsa_token(
        key, json.dumps({"alg": "RS256", "typ": "JWT", "kid": "rsa-a", **extra}).encode(),
        json.dumps(claims()).encode(),
    )
    await denied(verifier(source), value)
    assert source.calls == 0


@pytest.mark.parametrize(
    "value", [None, "", "a.b", "a.b.c.d", "a.b.!", "ü.b.c", "a" * (MAX_TOKEN_BYTES + 1)],
    ids=["null", "empty", "two-segments", "four-segments", "invalid-signature", "unicode", "oversize"],
)
async def test_bounded_malformed_input_never_fetches_keys(signing_keys, value):
    source = Provider([public_jwk(signing_keys["rsa-a"], "rsa-a")])
    await denied(verifier(source), value)
    assert source.calls == 0


async def test_oversized_header_and_duplicate_claims_rejected(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    verifier_ = verifier(source)
    await denied(verifier_, token(key, headers={"padding": "x" * 1200}))
    original = token(key)
    header, payload, signature = original.split(".")
    duplicate_header = base64.urlsafe_b64encode(b'{"alg":"RS256","alg":"RS256","kid":"rsa-a"}').decode().rstrip("=")
    await denied(verifier_, f"{duplicate_header}.{payload}.{signature}")
    duplicate_claim = base64.urlsafe_b64encode(b'{"sub":"a","sub":"b"}').decode().rstrip("=")
    await denied(verifier_, f"{header}.{duplicate_claim}.{signature}")
    assert source.calls == 0


@pytest.mark.parametrize("value", ["missing", "", "00000000-0000-0000-0000-000000000000", UUID(int=0)])
async def test_invalid_selected_scope_rejected(signing_keys, value):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    await denied(verifier(source), token(key), status=400, project=value)
    assert source.calls == 0


async def test_disabled_by_default(signing_keys):
    source = Provider([public_jwk(signing_keys["rsa-a"], "rsa-a")])
    verifier_ = IdentityVerifier(issuer=ISSUER, audience="authenticated", key_provider=source)
    await denied(verifier_, token(signing_keys["rsa-a"]), status=503)
    assert source.calls == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "http://insecure.example/auth/v1"},
        {"issuer": ISSUER + "/"},
        {"issuer": "https://user:secret@fixture.example/auth/v1"},
        {"audience": ""},
        {"jwks_url": "http://retention-fixture.supabase.co/jwks"},
        {"jwks_url": "https://attacker.example/jwks"},
        {"jwks_url": JWKS_URL + "?chosen=key"},
        {"jwks_url": JWKS_URL + "#fragment"},
        {"algorithms": ("HS256",)},
        {"algorithms": ()},
        {"cache_ttl_seconds": 601},
        {"refresh_cooldown_seconds": 0},
        {"fetch_timeout_seconds": 11},
        {"clock_skew_seconds": 61},
        {"fetch_timeout_seconds": float("nan")},
    ],
)
def test_invalid_server_configuration_rejected(overrides):
    config = {"issuer": ISSUER, "audience": "authenticated", "jwks_url": JWKS_URL, **overrides}
    with pytest.raises(ValueError):
        IdentityVerifier(**config)


def test_exactly_one_explicit_trusted_key_source_required():
    with pytest.raises(ValueError):
        IdentityVerifier(issuer=ISSUER, audience="authenticated")
    with pytest.raises(ValueError):
        IdentityVerifier(issuer=ISSUER, audience="authenticated", jwks_url=JWKS_URL, key_provider=Provider([]))


async def test_server_algorithm_subset_and_key_algorithm_binding(signing_keys):
    rsa_key, ec_key = signing_keys["rsa-a"], signing_keys["ec-a"]
    source = Provider([public_jwk(rsa_key, "rsa-a"), public_jwk(ec_key, "ec-a")])
    verifier_ = verifier(source, algorithms=("RS256",))
    await denied(verifier_, token(ec_key, "ec-a"))
    assert source.calls == 0
    verifier_ = verifier(source)
    await denied(verifier_, token(ec_key, "rsa-a"))
    assert source.calls == 1


async def test_one_jwks_fetch_for_concurrent_valid_requests(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    verifier_ = verifier(source)
    results = await asyncio.gather(*(verifier_.verify(token(key), TENANT, PROJECT) for _ in range(20)))
    assert len(results) == 20
    assert source.calls == 1


async def test_unknown_kid_rate_limited_without_attacker_sized_cache(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    verifier_ = verifier(source)
    for count in range(25):
        await denied(verifier_, token(key, kid=f"missing-{count}"))
    assert source.calls == 1
    assert len(verifier_._keys) == 1


async def test_rotation_replaces_old_keys_and_accepts_new_key(signing_keys):
    old_key, new_key = signing_keys["rsa-a"], signing_keys["rsa-b"]
    source = Provider([public_jwk(old_key, "rsa-a")])
    verifier_ = verifier(source)
    assert await verifier_.verify(token(old_key), TENANT, PROJECT)
    source.value = {"keys": [public_jwk(new_key, "rsa-b")]}
    # Simulate expiry of the documented unknown-key refresh cooldown.
    verifier_._refresh_after = 0
    assert await verifier_.verify(token(new_key, "rsa-b"), TENANT, PROJECT)
    await denied(verifier_, token(old_key))
    assert source.calls == 2


async def test_rotation_fetch_failure_does_not_retain_old_trust(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    verifier_ = verifier(source)
    assert await verifier_.verify(token(key), TENANT, PROJECT)
    source.error = RuntimeError("do-not-expose-provider-secret")
    verifier_._refresh_after = 0
    await denied(verifier_, token(signing_keys["rsa-b"], "rsa-b"), status=503)
    await denied(verifier_, token(key), status=503)
    assert source.calls == 2
    assert verifier_._keys == {}


async def test_same_kid_replacement_needs_expiry_or_server_purge(signing_keys):
    old_key, replacement = signing_keys["rsa-a"], signing_keys["rsa-b"]
    source = Provider([public_jwk(old_key, "rsa-a")])
    verifier_ = verifier(source)
    assert await verifier_.verify(token(old_key), TENANT, PROJECT)
    source.value = {"keys": [public_jwk(replacement, "rsa-a")]}
    await denied(verifier_, token(replacement, "rsa-a"))
    assert source.calls == 1  # Bad signatures cannot cause a refresh storm.
    await verifier_.invalidate_cache()
    assert await verifier_.verify(token(replacement, "rsa-a"), TENANT, PROJECT)
    await denied(verifier_, token(old_key, "rsa-a"))
    assert source.calls == 2


async def test_expired_cache_fails_closed_and_can_be_purged(signing_keys):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    verifier_ = verifier(source)
    assert await verifier_.verify(token(key), TENANT, PROJECT)
    source.error = RuntimeError("unavailable")
    verifier_._expires_at = verifier_._refresh_after = 0
    await denied(verifier_, token(key), status=503)
    source.error = None
    await verifier_.invalidate_cache()
    assert await verifier_.verify(token(key), TENANT, PROJECT)
    assert source.calls == 3


async def test_provider_timeout_bounded_and_cancellation_propagates(signing_keys):
    async def slow():
        await asyncio.sleep(30)
        return {"keys": []}

    verifier_ = verifier(slow, fetch_timeout_seconds=0.02)
    await denied(verifier_, token(signing_keys["rsa-a"]), status=503)
    started = asyncio.Event()

    async def blocked():
        started.set()
        await asyncio.Event().wait()

    verifier_ = verifier(blocked)
    pending = asyncio.create_task(verifier_.verify(token(signing_keys["rsa-a"]), TENANT, PROJECT))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {"keys": []},
        lambda value: {"keys": value["keys"] * 33},
        lambda value: {"keys": value["keys"] * 2},
        lambda value: {"keys": [None]},
        lambda value: {"keys": [{**value["keys"][0], "d": "private-key-data"}]},
        lambda value: {"keys": [{**value["keys"][0], "alg": "HS256"}]},
        lambda value: {"keys": [{**value["keys"][0], "use": "enc"}]},
        lambda value: {"keys": [{**value["keys"][0], "key_ops": ["sign"]}]},
        lambda value: {"keys": [{**value["keys"][0], "x5u": "https://attacker.example/key"}]},
        lambda value: {"keys": [{**value["keys"][0], "kty": "EC"}]},
        lambda value: {"keys": [{**value["keys"][0], "n": "bad-key"}]},
        lambda value: {"keys": [{**value["keys"][0], "n": "A" * 1_367}]},
        lambda value: {"keys": [{**value["keys"][0], "e": "A" * 9}]},
        lambda value: {**value, "padding": "x" * MAX_JWKS_BYTES},
    ],
)
async def test_invalid_or_oversized_jwks_fails_closed(signing_keys, mutation):
    key = signing_keys["rsa-a"]
    source = Provider([public_jwk(key, "rsa-a")])
    source.value = mutation(source.value)
    await denied(verifier(source), token(key), status=503)


async def test_weak_rsa_and_wrong_ec_curve_rejected(signing_keys):
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    wrong_curve = ec.generate_private_key(ec.SECP384R1())
    for key, kid in ((weak, "rsa-a"), (wrong_curve, "ec-a")):
        source = Provider([public_jwk(key, kid)])
        await denied(verifier(source), token(signing_keys["rsa-a"], kid), status=503)


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, value):
        self.value = value

    async def __aiter__(self):
        for offset in range(0, len(self.value), 4096):
            yield self.value[offset:offset + 4096]


def http_verifier(handler):
    return IdentityVerifier(
        issuer=ISSUER, audience="authenticated", jwks_url=JWKS_URL,
        enabled=True, clock_skew_seconds=0, http_transport=httpx.MockTransport(handler),
    )


async def test_https_fetch_uses_only_fixed_url_and_sends_no_bearer(signing_keys):
    key = signing_keys["rsa-a"]
    requests = []

    async def handler(request):
        requests.append(request)
        body = json.dumps({"keys": [public_jwk(key, "rsa-a")]}).encode()
        return httpx.Response(200, stream=BytesStream(body))

    verifier_ = http_verifier(handler)
    assert await verifier_.verify(token(key), TENANT, PROJECT)
    assert len(requests) == 1
    assert str(requests[0].url) == JWKS_URL
    assert "authorization" not in requests[0].headers
    assert requests[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    "status, headers, body",
    [
        (302, {"location": "https://attacker.example/jwks"}, b""),
        (503, {}, b"unavailable"),
        (200, {"Content-Length": str(MAX_JWKS_BYTES + 1)}, b"{}"),
        (200, {"Content-Encoding": "gzip"}, b"compressed"),
        (200, {}, b"x" * (MAX_JWKS_BYTES + 1)),
        (200, {}, b'{"keys":[],"keys":[]}'),
        (200, {}, b"not json"),
    ],
    ids=["redirect", "unavailable", "size-header", "compressed", "oversize-body", "duplicate", "malformed"],
)
async def test_network_failures_and_limits_are_closed(signing_keys, status, headers, body):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, stream=BytesStream(body))

    await denied(http_verifier(handler), token(signing_keys["rsa-a"]), status=503)
    assert len(requests) == 1  # A redirect is never followed.


async def test_http_timeout_does_not_become_authentication(signing_keys):
    async def handler(request):
        raise httpx.ReadTimeout("fixture timeout", request=request)

    await denied(http_verifier(handler), token(signing_keys["rsa-a"]), status=503)
