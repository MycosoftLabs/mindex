# Retention v1 verified identity

`mindex_api/retention/identity.py` authenticates the Supabase user at the private
MINDEX retention boundary. It produces the shared frozen
`Principal(issuer, subject, tenant_id, project_id)`. It does **not** authorize a
tenant, project, artifact, job, cache result, download, or memory reference.
Every repository operation must recheck current server-owned membership using
all four principal fields before returning or changing a private record. A
cryptographically verified token containing a project claim still does not
establish membership.

## Integration API

```python
from mindex_api.retention.identity import IdentityVerifier

verifier = IdentityVerifier(
    issuer="https://<approved-project>.supabase.co/auth/v1",
    audience="authenticated",
    jwks_url="https://<approved-project>.supabase.co/auth/v1/.well-known/jwks.json",
    enabled=True,  # Default is False; enable only with reviewed server config.
)
principal = await verifier.verify(original_user_jwt, requested_tenant_uuid, requested_project_uuid)
# The repository must now authorize principal against its current memberships.
```

Construction requires exactly one fixed HTTPS JWKS URL or a trusted server
`key_provider: async () -> Mapping[str, Any]` returning a JWKS `{"keys": [...]}`.
The provider receives no user input. It is useful for offline fixtures and
reviewed adapters, and is not an alternative identity authority selected by the
caller. Issuer, audience, algorithms and URL are server configuration. The module
does not read browser owner fields, unsigned `X-User-Id`, token-provided URLs,
environment variables or global authentication state.

The BFF/MAS may relay the original user bearer JWT over a protected service hop.
MINDEX verifies it again. A service key or operator identity does not impersonate
a user. A future delegated credential needs its own reviewed signing and scope
contract; it must not be implemented by accepting an unsigned forwarded user ID.
Do not place user tokens in URLs, public logs, ledger metadata, task summaries or
memory references. Do not reuse a principal across requests or users.

Required runtime packages are `PyJWT[crypto]>=2.10.1,<3` and the existing `httpx`.
The caller must install the repository's retention dependency extra before
enabling this route. Missing/bad configuration must leave the route unavailable.

## Verification policy

- Only the server's explicit subset of `RS256` and `ES256` is allowed. HMAC,
  unsigned tokens, algorithm/key-type mismatch and unapproved curves are denied.
  RSA keys must be 2048–8192 bits; ES256 requires P-256.
- The signature, exact issuer, exact single-string audience, `exp`, `iat`, and
  optional `nbf` are verified. `exp` and `iat` are required integer timestamps;
  the lifetime must be positive. The default clock tolerance is 30 seconds,
  bounded to 60 seconds. `nbf` is optional because Supabase does not require it.
- `sub` must be a non-zero UUID, `role` must be exactly `authenticated`, and
  `is_anonymous` must be the boolean `false`. Missing anonymous status fails
  closed. `anon`, `service_role`, operator and anonymous user tokens are denied.
- Requested tenant/project UUIDs are normalized into the principal. Token claims
  and user metadata cannot override them or manufacture server membership.
- Token-selected `jku`, `jwk`, `x5u`, `x5c`, critical headers and unencoded-payload
  extensions are rejected. Duplicate JSON properties, non-finite numbers,
  ambiguous numeric types, invalid compact encodings and invalid key IDs fail.

Supabase documents asymmetric JWKS discovery and recommends established JWT
libraries for verification. Anonymous signed-in users also carry the
`authenticated` role, so checking role alone is insufficient.
Sources: [Supabase JWT guide](https://supabase.com/docs/guides/auth/jwts),
[anonymous users](https://supabase.com/docs/guides/auth/auth-anonymous),
[claim reference](https://supabase.com/docs/guides/auth/jwt-fields),
[PyJWT verification API](https://pyjwt.readthedocs.io/en/stable/api.html).

## Resource and rotation limits

| Boundary | Limit or behavior |
| --- | --- |
| Compact JWT | 16,384 ASCII bytes; three nonempty base64url segments |
| Decoded JOSE header | 1,024 bytes; key ID at most 128 characters |
| JWKS endpoint | Fixed HTTPS URL, same origin as configured issuer, no credentials/query/fragment |
| Network | Verified TLS by default, no environment proxy, no redirects, no bearer forwarded |
| JWKS body | 65,536 bytes streamed in 4,096-byte chunks; compressed responses denied |
| Keys | At most 32; unique key IDs; public asymmetric verification keys only |
| Fetch | Default 5-second total timeout, including provider/network work; maximum 10 seconds |
| Cache | Default 300-second TTL; maximum 600 seconds; no indefinite per-key cache |
| Refresh | One fetch at a time; default 30-second cooldown for unknown IDs and failed fetch retries |

An unknown key ID can cause a refresh only after the cooldown. A new key may
therefore be rejected during that interval. A complete successful refresh
replaces all previous keys. A failed refresh removes the local key cache and
returns unavailable; stale keys never extend their TTL during an outage. Known
key IDs with invalid signatures do not trigger refreshes. Replacement under the
same key ID requires cache expiry or a trusted server call to
`await verifier.invalidate_cache()`.

Supabase may cache its own JWKS response upstream. Local invalidation does not
purge an upstream cache, revoke an issued session or prove immediate account
revocation. Supabase's [signing key guidance](https://supabase.com/docs/guides/auth/signing-keys)
describes this rotation/revocation delay. Current project membership must still
be rechecked on every operation; any requirement for immediate session revocation
needs a reviewed live introspection/revocation integration. No such live check is
claimed here.

## Failure contract

`RetentionError.code/status` returns `unauthorized/401` for invalid user tokens,
`invalid_scope/400` for malformed selected UUIDs, and `identity_unavailable/503`
for disabled verification, JWKS fetch/parse failure or expired-cache retry
backoff. Error text contains only the stable code. Token contents, crypto error
details and provider exception messages are not returned. Invalid constructor
configuration raises `ValueError` during service setup. Task cancellation
propagates without becoming an authenticated or success-shaped result.

## Reproducible verification and qualification

```powershell
# From this MINDEX checkout, using an environment with the retention/test extras:
python -m pytest tests/test_retention_identity.py -o addopts='' -q
```

The suite creates fresh RSA/EC private keys only in test memory, signs actual
JWTs, and performs real local signature verification. Injected JWKS providers
and HTTP transports are explicitly offline fixtures. It covers valid RSA/EC,
invalid/expired/missing claims, issuer/audience/algorithm mismatch, anonymous and
service identities, forged claims and headers, chosen URLs, scope confusion,
malformed/bounded input, concurrent cache use, unknown-key storms, rotation,
revocation cache purge, key mismatch/weak keys, timeouts, cancellation, redirects,
provider failures and response limits. These are identity-boundary tests, not
evidence of a deployed Supabase integration or repository authorization.

Local result on October 1, 2026: **107 passed in 0.50 seconds**, Python 3.12.10,
PyJWT 2.15.1, cryptography 48.0.0, and httpx 0.27.2. The execution environment was
`C:/Users/Owner1/.codex/worktrees/brief09-shared-retention/runtime/Scripts/python.exe`.
This test duration is not a production throughput or latency benchmark. Scoped
new-file whitespace checks passed; the checkout-wide whitespace check reports
pre-existing changes in the four `mindex_test*_utf8.txt` capture files.

Before live qualification, confirm the owner-approved issuer/audience and
membership source; configure an asymmetric Supabase project; test real access
tokens from two unrelated users and two projects against the repository and
all private read/write/cache/stream routes; rehearse key rotation and membership
revocation; verify BFF/MAS relay logs contain no tokens. No production changes,
session revocation, migrations, deploys or live credentials are performed by
this implementation. Rollback disables the retention route and restores the
previous application version; this module makes no identity-system mutations.

Baseline: MINDEX commit `42b876fcfca2e86b0365e8fe8afab628d6a94705`, isolated branch
`codex/brief09-shared-retention`. Assigned files are this document,
`mindex_api/retention/identity.py`, and `tests/test_retention_identity.py`; shared
contracts and repository membership policy are owned separately by brief 09.
