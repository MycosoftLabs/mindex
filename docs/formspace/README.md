# FormSpace durable experiments backend — brief 04

This backend retains owned scalar experiment requests, immutable chart revisions,
compute jobs, leases, and canonical artifact references in MINDEX. It consumes
brief 09 retention.v1 identity, authoritative membership, private artifact archive,
and memory reference APIs. It creates no user identity or independent blob store.

## Source and ownership

Source repository: `https://github.com/MycosoftLabs/mindex.git`.
Baseline: `42b876fcfca2e86b0365e8fe8afab628d6a94705`.
Integrated frozen shared-retention substrate: commit `052605a` (Brief 09).
Isolated checkout: `C:/Users/Owner1/.codex/worktrees/formspace-durable/mindex`.
Branch: `codex/formspace-durable-jobs`. No push, PR, deployment, or live migration.

Implementation scope: `mindex_api/formspace/`,
`mindex_api/routers/formspace_durable.py`, minimal `mindex_api/main.py` registration,
the additive migration, pinned `rfc8785==0.1.4` dependency, FormSpace tests/fixtures,
and this documentation. Existing checkout line-ending differences in four tracked
`mindex_test*_utf8.txt` logs are excluded from the commit and remain untouched.

The engineering assignment was dispatched October 1, 2026. Its original files are
`04-formspace-durable-experiments.md`, `00-shared-engineering-contract.md`, and
`dispatch/EXECUTION-CONTRACT.md` in the company-application-catalog handoff.
The required boundaries are: owned input/chart/run evidence in MINDEX; original
Supabase JWT; server-owned membership; atomic admission/outbox; scalar math named
truthfully; private verified archive bytes; independent memory/index/replica
states; fixture evidence separated from live qualification; manual release only.

## Request → computation → retained readback

1. Website/MAS relays the original Supabase bearer JWT and `X-Tenant-Id` /
   `X-Project-Id` selectors. Shared retention verifies token signature, issuer,
   audience, expiry and permanent authenticated role; membership is rechecked
   and share-locked by `authorize_in_session` in every app transaction. Neither
   service keys nor `X-User-Id` establish ownership.
2. `POST /api/mindex/formspace/v1/jobs`, `Idempotency-Key`, body `{request: ...}`
   validates the exact `formspace.experiment.request/v1` DTO. The immutable chart,
   dataset, parameters, request hashes, job, and outbox commit in one transaction.
   Replay returns the original ID; a changed body under that key returns 409.
   An existing chart ID/revision with changed content returns 409 independently.
3. The separately authenticated TypeScript worker claims a job. The stored owner
   determines every later operation. No user JWT is stored. A 120-second lease
   and monotonic fence reject crashed/stale workers; lease expiry is checked with
   live `clock_timestamp()` after membership locks, not transaction-start time.
4. `/computed` validates approved engine-code identity, request/chart/dataset/
   parameter hashes, exact canonical output bytes, strict result fields and
   scalar result consistency. It stores bytes before acknowledging `archiving`.
   The TypeScript engine owns recurrence execution; backend lineage/shape checks
   are not an independent engine implementation.
5. `/reconcile` idempotently admits those persisted bytes to shared retention with
   key `formspace:<job_id>`. Only shared `state=verified` plus exact authorized
   `content()` readback allows completion. Membership and the fence are checked
   again after archive I/O. A cancellation or revocation wins that race.
6. Authorized `/result` checks exact bytes/hash/version, with `X-Content-SHA256`
   and `X-Artifact-Version`. `/input` returns the complete immutable request with
   `X-Input-SHA256` and `X-Input-Durability: postgres_committed`, including after
   cancellation. PostgreSQL input retention is not described as an AWS backup.
7. Memory reference readback is independently retryable via `POST /jobs/{id}/memory`.
   `memory.reference_state=verified` does not change `memory.state` or
   `memory.index_state` from pending: this package has no verified MYCA indexing
   receipt. `learned=false`. `replica.state=unavailable` until a reviewed NAS
   replica adapter provides exact-byte/version proof.

Chart revisions also have a chart-only path through the same immutable table.
`POST /charts` saves a strict `formspace.chart-revision/v1` definition under
the caller's authenticated issuer/subject/tenant/project. Identical retries
return the existing revision; a changed definition at the same chart ID and
revision returns 409. `GET /charts` lists at most 100 owned revisions, while
`GET /charts/{chart_id}?revision=N` reads the exact definition and hash. Each
operation reauthorizes shared membership. Chart-only save does not persist a
dataset; job admission still links its request to the same revision. The
existing `formspace.chart_revision` table is reused, so no new migration is
needed.

## HTTP contracts

Customer endpoints (original user JWT and selected scope):

| Method/path under `/api/mindex/formspace/v1` | Response |
| --- | --- |
| `POST /jobs` | 202 new / 200 identical replay; `formspace.job/v1` |
| `GET /jobs?limit=50` | `formspace.jobs/v1`, `jobs` list; max 100 |
| `POST /charts` | 200 immutable chart save or identical replay; `formspace.chart/v1` |
| `GET /charts?limit=100` | `formspace.charts/v1`; max 100 exact revisions |
| `GET /charts/{chart_id}?revision=N` | Exact owner-scoped chart definition/hash or 404 |
| `GET /jobs/{id}` | Scoped receipt; unknown and cross-owner IDs return 404 |
| `GET /jobs/{id}/input` | Exact canonical immutable input and verified request hash |
| `GET /jobs/{id}/result` | Exact verified output, or 409 pending/unavailable |
| `POST /jobs/{id}/cancel` | Cancellation wins active lease; terminal completed/failed returns 409 |
| `POST /jobs/{id}/memory` | Retry exact memory reference proof independently |

Worker endpoints use `X-FormSpace-Worker-Token` (configured ≥32 characters).
This credential dispatches only already-admitted owned jobs; it cannot create
ownership, establish membership, or submit arbitrary owner IDs.

| Method/path | Request / response |
| --- | --- |
| `POST /worker/claim` | `{worker_id}` → `{job,request,has_output,lease:{token,fence,expires_at}}` or null |
| `POST /worker/jobs/{id}/heartbeat` | `{lease_token,fence}` → job receipt |
| `POST /worker/jobs/{id}/computed` | lease + `{result_json,output_sha256}` → archiving receipt |
| `POST /worker/jobs/{id}/reconcile` | lease → archiving/completed receipt; pending releases lease with backoff |
| `POST /worker/jobs/{id}/fail` | lease + `{error_code}` → safe terminal/retry receipt |

Error codes for failure submission: `engine_failed`, `invalid_result`,
`worker_timeout`, `retention_unavailable`. A retention outage preserves computed
bytes and schedules retry. Stale/expired/cancelled leases return 409 `lease_lost`;
revoked membership returns 403. Errors never include request bodies or tokens.
Every response uses private/no-store cache policy and scope-aware `Vary`.

Bounds: 1 MiB request, 4096 samples, 2 MiB exact result, scalar input/state
parameter magnitudes ≤1e6 (per-domain dt/a ranges also enforced), output ≤1e18;
32 admitted/active jobs per project; at most 4 active leases per project and 16
globally; 8 pre-computation attempts. Archive retries retain their outbox until
verified/cancelled instead of discarding evidence. Request reads time out at 15s.
The approved engine hash is pinned by `FORMSPACE_ENGINE_CODE_SHA256`; runtime
source hashing means a different TypeScript build/runtime may need a new review.

## Reproduce local evidence

From this repository with Python 3.11+ and project test dependencies installed:

```powershell
# Provision two new, empty, disposable loopback databases for this run, then set:
$env:FORMSPACE_TEST_DATABASE_URL = 'postgresql+asyncpg://<user>@127.0.0.1:<port>/formspace_fixture_<suffix>'
$env:RETENTION_TEST_DSN = 'postgresql://<user>@127.0.0.1:<port>/retention_fixture_<suffix>'
$env:RETENTION_TEST_ALLOW_DISPOSABLE = '1'
python -m pytest tests/test_formspace_durable.py tests/test_formspace_durable_postgres.py tests/test_formspace_durable_api_postgres.py -q
python docs/formspace/benchmark.py
python -m compileall -q mindex_api/formspace mindex_api/routers/formspace_durable.py
git diff --check -- mindex_api/formspace mindex_api/routers/formspace_durable.py migrations/20261001_formspace_durable.sql tests/test_formspace_durable.py tests/test_formspace_durable_postgres.py docs/formspace
```

Measured on October 1, 2026: **34 tests passed** across the explicit fixture,
native PostgreSQL transaction suite, and signed-JWT HTTP-to-worker suite (JUnit
receipt: `tests.xml`). The PostgreSQL tests used two newly created disposable
local databases. The end-to-end test crossed the actual FastAPI shared-auth
dependency, signed synthetic Supabase-compatible JWTs, shared retention tables,
the actual TypeScript worker in a separate process, a fake S3 adapter, and
authorized exact-byte/version readback. It also exercised expired, invalid,
wrong-audience and wrong-role tokens; two users/projects; replay/conflict;
transaction rollback; archive retry; reference-only memory state; process death
after commit; cancellation; and membership revocation. Credentials and all
records were synthetic. This does not qualify a live Supabase issuer, AWS S3/KMS,
Object Lock, production IAM, MYCA indexing, or NAS replication.

The chart-only follow-up added owner-scoped save/list/read routes, immutable
same-revision conflict handling, and two-user/two-project checks. Its explicit
in-memory service-boundary run passed 33 tests. An earlier native PostgreSQL
fixture attempt skipped because no disposable database was configured, and the
signed-JWT HTTP test initially could not collect because the default interpreter
lacked PyJWT. PyJWT 2.15.1 is available through the existing Brief 09 runtime;
collection now succeeds for the one signed-JWT/PostgreSQL/worker test. Execution
is waiting for a resource-queue slot before starting a new task-owned PostgreSQL
17.11 fixture. The test is prepared to assert actual SQL owner scope, immutable
replay/conflict and trigger behavior, nonmember denial, membership revocation,
chart readback after separate worker processes, and persisted bytes. No test
server has been started for this follow-up yet. These checks do not qualify live
service behavior.

The copied canonical fixtures were generated by the sibling website domain
implementation on Node v24.15.0 / tsx 4.22.4. Their source paths are
`website/tests/formspace-durable/fixtures/chain-*.json`; attribution and engine
identity are in `chain-fixture-metadata.json`. The 32-step teaching fixture,
perturbation delta 1.2 at index 3, crosses residual 0.05 at index 21. This is a
deterministic scalar recurrence demonstration, not measured ecological recovery,
a general exact ODE solver, laboratory acquisition, trained model, or live data.

`benchmark-receipt.json` records a local 32-sample, 5276-byte contract parse/hash
benchmark: first parse 1.315 ms, warm p50 0.458 ms, p95 0.849 ms, 1964 operations/s,
35,831,808-byte process peak working set. Windows 11, Python 3.12.10, 24 logical
CPUs, Intel64 family 6 model 183. These are fixture CPU measurements only; no
database, network, archive, MYCA or NAS performance was measured, and no production
performance target is claimed achieved.

## PostgreSQL fixtures, restore evidence, and remaining qualification gates

`FORMSPACE_TEST_DATABASE_URL` is the only opt-in database input. It must name an
empty, disposable local `formspace_fixture_<suffix>` database with asyncpg DSN.
The test refuses remote addresses, query overrides and existing app schemas,
never uses inherited app `DATABASE_URL`, and never drops data. It applies the
actual FormSpace migration and uses shared retention's real membership checker
against an explicitly minimal membership fixture. It exercises concurrent replay,
single outbox, immutable revisions, restart, claim exclusion, recovery fencing,
cancellation and a membership-lock wait that outlives a lease. It leaves schemas
for review; reruns use a new empty disposable database.

Local evidence: PostgreSQL 17.11 ran on loopback port 55909 as a shared,
pre-existing fixture. The native transaction and HTTP-to-worker tests passed
against isolated disposable databases `formspace_fixture_brief04_transactions_final`
and `retention_fixture_brief04_api_final`. The database owner later stopped the
shared server. To preserve that boundary, a task-owned PostgreSQL 17.11 cluster
on loopback port 55917 independently restored the schema-scoped custom dump into
two empty databases. All 10 tables and 16 rows had matching canonical row hashes;
the verified artifact version, content hash and metadata hash also matched.
Details are in `restore-comparison.json`. The previous shared restore target
itself could not be queried after that server stopped, so direct comparison to
that database remains open. The artifact row has no local payload bytes; FakeS3
objects are outside the dump and were not restored or re-read.

Required before actual end-to-end qualification:

- If direct comparison to the prior shared restore target is still required,
  have the coordinator provide its preserved local fixture checkpoint for an
  isolated copy. Do not restart or reuse the stopped shared service.
- Establish server-owned membership provisioning/revocation authority and test
  two real Supabase identities/two projects at the actual HTTP boundary.
- Pin approved worker image/source/code hash; exercise worker process death and
  restored database outbox recovery in the integrated staging environment.
- Verify actual AWS KMS/Object Lock/exact-version readback through brief 09,
  production restore durability for the admitted chart/dataset PostgreSQL records, MYCA
  indexing receipt/readback, and NAS exact replica proof if required.

## Migration, cancellation, and manual release handoff

`migrations/20261001_formspace_durable.sql` is additive and intended for a reviewed
manual migration after shared retention. It creates only the `formspace` schema,
tables/indexes and immutability triggers, revoking PUBLIC schema/table access.
The app service role must have reviewed access to both schemas and no authority
to provision membership. Public/browser roles receive no direct access. No
production migration, deployment or integration is performed by this package.

Cancellation is a compute lifecycle action: it fences work and clears temporary
app output bytes. Immutable admitted input remains readable to its owner.
An artifact already admitted before cancellation may remain in the owner's
canonical private retention namespace; deletion uses the separate reviewed
retention policy/API. Cancellation does not claim physical deletion or key erasure.

Cursor's separate manual blue/green plan must preserve old environment and
database, use immutable source/image/migration identifiers, snapshot and prove
restore before schema change, verify health/membership/auth/byte-hash boundaries,
then request the authorized traffic switch. Set `FORMSPACE_DURABLE_ENABLED=true`
only with integrated retention config, worker credential and approved engine hash.
Rollback disables admission/worker dispatch and restores the previous image and
traffic target while preserving the additive schema and evidence. Image rollback
does not undo migrations. Do not drop the schema to roll back; any data removal
requires a separately reviewed retention/recovery action.

Manual review: authenticated user imports series, receives admission, refreshes
and downloads its exact immutable input; a second user/project gets no rows/404;
worker restart resumes one fenced attempt; cancellation blocks late completion;
archive pending never enables verified result; successful readback downloads exact
bytes/version; memory outage leaves result usable and indexing pending. Inspect
the website's keyboard/theme/reduced-motion behavior in its separate handoff.
