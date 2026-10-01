# Brief 09 delivery and manual qualification handoff

The October1 implementation assignment supersedes the copied brief's historical
"proposed/not dispatched" header. Implemented in an isolated local clone of
`MycosoftLabs/mindex`, branch `codex/brief09-shared-retention`, baseline
`42b876fcfca2e86b0365e8fe8afab628d6a94705`. No dirty audit patches were imported.
The public capture adapter, canonical history implementation, parent artwork,
taxonomy/genome patches, and Droid mission intake remain untouched.

## Implemented boundary map

| Layer | Implementation and evidence | Remaining qualification |
| --- | --- | --- |
| BFF/shared SDK | Versioned TypeScript/Python clients relay original user token and selected scope; runtime receipt checks, bounded bytes, hash validation and post-download reauthorization.12 actual TypeScript client tests. | Sibling application integration and real browser account switching. Their local vendor copies must pin the canonical SDK hash and converge to one shared package at integration. |
| Identity | Fixed issuer/audience RSA/EC JWT/JWKS verifier, bounded cache/fetch, strict role and nonanonymous user.107 signed-token cases. No unsigned user/role header authority. | Owner-approved Supabase issuer/audience, membership mirror provision/revoke lifecycle, live session revocation policy. |
| MINDEX API | Actual private HTTP handlers with streaming admission limits, per-operation membership, no-store projections, bounded subscriptions, unavailable/pending/error states.9 end-to-end local boundary cases. | Nonproduction routed MINDEX identity/storage configuration; default remains disabled. |
| Authoritative storage | Additive membership/grant/artifact/job/outbox/memory/orphan schema, atomic admission, quota/idempotency, immutable bytes, leases/fencing, revoke/delete/expiry.28 native PostgreSQL cases plus dump/restore. | Review migration and least-privilege deployment roles, volume/WAL/backup retention, real backup/restore acceptance. |
| Archive | Private S3 bucket controls, expected owner, KMS, conditional immutable versions, COMPLIANCE retention, exact version/hash/size readback, deferred exact deletion.79 fake-error/SDK-shape tests. | Actual AWS IAM/bucket/KMS/Object Lock tests, retained-version inventory and physical erasure reconciliation, lease latency under live failures. |
| MYCA memory | Canonical MINDEX reference plus exact artifact and reference readback; Python `MycaMemoryAdapter`. Cross-user search/direct-ID denial, learned=false. | External MYCA coordinator/vector indexing is not wired or claimed. NAS replication and restored replicas are not implemented by this substrate. |

The complete **local fixture** path is signed JWT → actual FastAPI → real
PostgreSQL atomic admission → fake S3 through the real archive adapter → authorized
exact content → canonical MYCA memory reference/readback. It uses synthetic bytes
and signing keys. This is not deployed end-to-end acceptance with Supabase/AWS/MYCA.

## Reproduction and results

Runtime used: Python3.12.10; FastAPI0.111.1, Starlette0.37.2, httpx0.27.2,
SQLAlchemy2.0.49, asyncpg0.29.0, psycopg3.1.20, pytest8.4.2,
pytest-asyncio0.23.8, PyJWT2.15.1, cryptography48.0.0, boto3/botocore1.43.30.
Node24.15.0. Scoped TypeScript checked with the existing TypeScript compiler from
the read-only website preview dependency tree; no full website build was run.

```powershell
# Use a managed Python environment with .[test,retention] installed.
# Start only a guarded local fixture per POSTGRES_QUALIFICATION.md.
$env:RETENTION_TEST_DSN='postgresql://retention_fixture@127.0.0.1:55909/retention_fixture_brief09'
$env:RETENTION_TEST_ALLOW_DISPOSABLE='1'
python -m pytest tests/test_retention_identity.py tests/test_retention_object_store.py tests/test_retention_postgres.py tests/test_retention_api.py -q -o addopts='' --tb=short
node --experimental-transform-types --test sdk/typescript/retention-v1.test.ts
tsc --noEmit --strict --skipLibCheck --target ES2022 --module ESNext --lib ES2022,DOM sdk/typescript/retention-v1.ts
python scripts/retention_manifest.py
```

Combined Python: **223 passed** in7.87s. TypeScript client:**12 passed**.
Scoped TypeScript:**zero diagnostics**. Actual `create_app()` registered the route
and returned503 with feature disabled, with no database/cloud call. Existing
Pydantic class-config and multipart import deprecation warnings remain. The four
`mindex_test*_utf8.txt` clone checkout conversion differences are excluded from
all commits. No full-repository test/build claim is made.

Native PG crash tests exit child processes before/after commit; stale fencing,
cross-owner denial, revoked grants, retries, corruption quarantine, and a database
failure remain fail-closed. The full boundary suite also revokes membership during
object readback and proves no bytes return. Reference and delta SHA256 manifests
are `REFERENCE_MANIFEST.json` and `FILE_MANIFEST.json`.

Measured local PostgreSQL baseline (4KiB payload; warm n50) is in
POSTGRES_QUALIFICATION.md: admission p50/p95=1.953/2.937ms, read=.772/1.061ms,
serial admission475.70/s, Python peakRSS59,092,992 bytes. This is not a WAN/S3,
concurrent production benchmark or an improvement claim. Dump/restore recovered
51 admitted artifacts and their exact208,896 bytes, jobs/outbox/membership.

## Release review

Read-only GitHub review found remote main at the same baseline and four workflow
files matching the checkout. Push workflows are scoped to main, so this task
branch does not dispatch them. Repository webhooks list was empty. Pull requests
run `platform-one-build`; its optional Iron Bank publish and agentic fallback
require secret names not present in the current repository secret metadata.
No secret values were accessed. The Supabase compliance workflow does not match
this patch's paths. Revalidate these conditions immediately before publication;
they are time-specific evidence, not permanent deployment safety.

No merge, deploy, production schema operation, paid engine/cloud launch, ledger
transaction, payment or hardware actuation is authorized. Use the manual
blue/green/rollback procedure in CONTRACT.md only after its external acceptance
gates have concrete evidence and the operator approves the traffic switch.

## Required manual inspection

1. Inspect schema and `authorize_in_session` with the deployment membership owner.
   Confirm runtime cannot edit membership identity/active state or grants; row
   locks need only the harmless timestamp-column privilege documented separately.
2. Inspect JSON artifact lineage and receipt states in FormSpace/NLM/Earth/MYCA
   consumers. Pending DB admission cannot be displayed as retained, indexed,
   trained, replicated or physically erased.
3. Review two unrelated users/two projects with real nonproduction sessions:
   admission/replay/conflict, guessed IDs, download/list/search/cancel/events,
   revocation after cached signing keys, and exact byte/memory readback.
4. Review actual private bucket/IAM/KMS/lock and expected account policy. Reconcile
   object versions after crash/cancellation; hard crashes before recording upload
   evidence still require inventory before any global erasure claim.
5. Restore metadata/pending bytes and referenced exact archive versions into a
   separate approved target, then measure RPO/RTO. Current native fixture restore
   does not qualify production backup coverage.

Legacy website `lib/auth/verified-identity.ts` was reviewed read-only. Its
user_metadata role/email/local-dev elevation and unsigned MAS user headers are
not accepted by this new boundary. Parent/sibling integration must not use it as
private-retention or operator authority; that existing application's broad legacy
authorization migration is separate from this shared contract change.
