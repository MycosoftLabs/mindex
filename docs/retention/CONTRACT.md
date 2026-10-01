# Shared identity, retention and memory v1

This is the brief 09 shared MINDEX substrate. It is opt-in and has not been deployed.
Supabase supplies asymmetric signed user identity. MINDEX owns membership decisions,
admitted private dataset/chart/artifact bytes, archive jobs/outbox, grants, receipts,
and memory references. The S3 archive is private, KMS-encrypted, versioned and
COMPLIANCE-locked. No second application database or public-source widening exists.

## Scope and authoritative identity

Every route is under `/api/mindex/retention/v1`. Relay the original Supabase access
JWT in `Authorization: Bearer ...`. `X-Tenant-Id` and `X-Project-Id` are UUID scope
selectors. They confer no authority. The verifier checks fixed issuer/audience,
signature, key algorithm, expiry, authenticated role and nonanonymous status.
Unsigned owner headers, service keys, user metadata and assistant text cannot
create a principal. The issuer's original user JWT is the current delegation
mechanism; this implementation does not mint a second signed delegation token.

`retention.membership(issuer,subject,tenant_id,project_id,active)` is a server-owned
authorization mirror. An operator-controlled synchronization/provisioning process
must be approved before live use. No enrollment/grant endpoint exists. Every
repository operation rechecks membership within its transaction. Same-project
users cannot read each other's artifacts without an explicit server-owned read
grant. That grant cannot cancel/delete the owner's work, grant operator powers,
or bypass membership. Operator and public-source domains remain distinct services.

JWT verification has a bounded signing-key cache, but membership is never cached.
Supabase session termination is not immediate cryptographic JWT revocation; until
expiry, a valid signed token still needs current active MINDEX membership. A live
session-revocation integration is an explicit external qualification gate.

## Versioned HTTP contract

All responses are `Cache-Control: private, no-store`, with `Vary` on authorization
and both scope selectors. Failure responses expose a stable code, never SQL,
provider exceptions or source bytes. A 200 alone is not archive or memory evidence.

| Method/path | Meaning |
| --- | --- |
| GET `/principal` | Current verified issuer/subject plus selected, authorized tenant/project. |
| POST `/artifacts` | Exact raw bytes; required `Idempotency-Key` and `Content-Type`; optional `X-Artifact-Kind=dataset\|chart\|artifact`, `X-Source-Event-At` aware ISO timestamp. Atomically commits payload, artifact, archive job and outbox. New admission 202; replay 200; changed bytes/metadata 409. |
| GET `/artifacts?query=&limit=50` | Literal metadata substring match in authorized scope, maximum100. Explicitly incomplete bounded list, no stable cursor/snapshot guarantee. |
| GET `/artifacts/{id}` | Safe receipt only, no bucket/key/version/lease/owner details. |
| GET `/artifacts/{id}/content` | **409 until verified.** Reads exact archived version, KMS/lock/hash/size evidence, then reauthorizes before returning octet-stream attachment. |
| GET `/artifacts/{id}/events` | At most12 status events over roughly60 seconds. Revalidates JWT and current authorization each iteration; denied guessed IDs never open a stream. |
| GET `/jobs/{id}` | Archive job receipt linked to its immutable artifact. Not a compute/inference completion claim. |
| POST `/jobs/{id}/cancel` | Owner-only durable cancellation, fences workers; completed jobs require explicit artifact deletion instead. |
| DELETE `/artifacts/{id}` | Revokes read/memory access and clears pending DB bytes immediately; physical locked-object purge is independently pending until retention expires. |
| POST `/memories` | JSON `{artifact_id,summary}`; exact authorized archive readback before creating a bounded MINDEX memory reference, then exact receipt readback. |
| GET `/memories/{id}` | Reauthorizes memory and artifact and verifies exact bytes; vector similarity is not consulted for grants. |
| GET `/memories?query=&limit=20` | Authorized literal summary search, maximum20, each candidate requires exact content/readback. It is not an unrestricted vector search. |

Status codes:401 invalid/missing token;403 missing/revoked membership;404 unowned,
expired, cancelled or deleted identifiers;409 replay conflict/pending content;
413 input too large;415 unsupported media/encoding;422 invalid input;429 pending
project quota;503 disabled/configuration/storage unavailable;504 archive timeout.
Payload is bounded while streaming before JSON parsing (maximum16MiB configuration
ceiling, default8MiB). No user-selected URL/bucket/KMS key/object key is accepted.

Receipt fields are defined in `sdk/typescript/retention-v1.ts` and the accompanying
JSON schema. `received_at` is the immutable database admission time;
`source_event_at` is separately source-reported or null; `available_at` is null
until required exact archive verification and final DB commit. `archive_verified`
is true only in state `verified`. `durability=postgres_committed` means precisely
that, not that backups or cloud storage are qualified. `learned=false` is explicit.
There is no global source-completeness watermark; per-artifact availability is not
normalization/indexing coverage. Brief01 owns canonical history/source coverage.

## SDK and backend integration

TypeScript: `new RetentionClient(origin, {accessToken,tenantId,projectId}, fetch?)`.
Use `principal`, `admit`, `getArtifact`, `getContent`, `list`, `getJob`, `cancel`,
`delete`, `remember`, `getMemory`. `getContent` checks declared size and SHA-256
and reauthorizes metadata after download. Create one client per request identity;
never share token-bearing clients across users. HTTPS is required off loopback.
Domain JSON may carry scientific/simulation provenance, but storage classification
remains private. No SDK writes a local cache/database or retries ambiguous POSTs
with a new idempotency key.

Python: `mindex_api.retention.client.RetentionClient` implements the same boundary;
`MycaMemoryAdapter` remembers/recalls only verified artifact references. It does
not write model weights or claim an external MYCA coordinator has indexed anything.

FastAPI integration imports `require_principal(request)` and
`get_retention_service(request)` from `mindex_api.routers.retention`. The dependency
returns `Principal(issuer,subject,tenant_id,project_id)` after live membership.
`service.admit(principal,metadata,payload)` delegates to atomic repository admission;
metadata is constructed with `admission_metadata(kind,key,media_type,event_at,config)`.
`await service.content(principal,id)` returns `(safe_receipt, exact_bytes)`.
`repository.authorize_in_session(session,principal)` holds the membership row lock
inside an application compute transaction. Workers reconstruct identity only from
their server-committed owner fields, never browser fields; do not store JWTs in jobs.

FormSpace/NLM compute-specific outboxes belong to those domain owners inside the
existing MINDEX database, with shared membership and retained result IDs. This
archive worker does not implement scientific compute, training, or operation grants.

## Environment and rollout

Install `.[retention]` with the application's managed Python environment. Defaults
disable the feature. Configure `RETENTION_ENABLED=true`,
`RETENTION_IDENTITY_ISSUER`, `RETENTION_IDENTITY_AUDIENCE`,
`RETENTION_IDENTITY_JWKS_URL` (trusted same HTTPS origin), and the existing MINDEX
database DSN. Archive settings: `RETENTION_BUCKET`, `RETENTION_PREFIX`,
`RETENTION_KMS_KEY` (exact key ARN), `RETENTION_EXPECTED_OWNER` (12-digit account),
`RETENTION_REGION`. Limits: `RETENTION_MAX_PAYLOAD_BYTES`,
`RETENTION_MAX_PENDING_BYTES`, `RETENTION_MAX_PENDING_COUNT`,
`RETENTION_LEASE_SECONDS`, `RETENTION_RETENTION_DAYS`. Do not put secret values in
docs, receipts, messages or source. No schema creation occurs during imports/startup.

Apply the reviewed additive migration only to an approved environment. Provision
separate schema-owner/membership administrator and least-privilege API/worker roles
as described in POSTGRES_QUALIFICATION.md. Private bucket and IAM qualification is
in ARCHIVE.md. Use `python -m mindex_etl.jobs.retention_worker --limit 10` for an
explicit bounded pass; it installs no scheduler and reports attempts, not successes.

Manual blue/green only: snapshot schema/metadata and exact object-version inventory;
rehearse the additive migration and restore on a disposable copy; deploy immutable
image/source versions with feature disabled; provision approved membership/IAM;
run two-user/two-project synthetic denied/allowed tests and pending-worker restart;
verify byte/memory readback; approve traffic switch explicitly; retain old environment.
Rollback disables admissions/workers and restores prior binaries while retaining
new tables, tombstones, queues, KMS/config and object versions. Image rollback does
not undo data. Never drop this schema as a production rollback.

## Qualification boundary

Local tests exercise actual JWT verification, HTTP handlers, PostgreSQL transactions,
leases/fencing, private archive adapter with synthetic S3, exact SDK/MYCA references,
and revocation/deletion. They do not establish live Supabase sessions, approved
membership synchronization, effective AWS account IAM, real Object Lock erasure,
NAS replication, external MYCA indexing or deployed application integration.

An upload surviving a stale finalization is recorded in an orphan cleanup ledger.
A hard process crash between S3 upload and recording its reference can leave an
unreferenced immutable version. Normal admitted work recovers via deterministic
key/conditional-write readback. Cancelled/deleted work during that crash window
requires operator version inventory reconciliation before physical erasure can be
certified. No receipt claims global erasure, proven RPO/RTO or production readiness.
