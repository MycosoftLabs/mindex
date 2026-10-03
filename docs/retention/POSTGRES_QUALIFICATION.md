# Private retention PostgreSQL qualification

Tested October 1, 2026 in the isolated MINDEX checkout on branch
`codex/brief09-shared-retention`, from baseline
`42b876fcfca2e86b0365e8fe8afab628d6a94705` plus the versionless-reconciliation
repair. The existing four dirty
`mindex_test*_utf8.txt` files were not edited. This document covers the additive
PostgreSQL repository, migration, and disposable runtime; root delivery documents
cover identity, API, object adapter, SDK, and the combined fixture vertical slice.

## Evidence and boundaries

`tests/test_retention_postgres.py`: **34 passed** against a real, local PostgreSQL
17.11 process on the isolated, marked port-55919 fixture. No SQLite substitution, production database, network database,
production migration, or installed PostgreSQL Windows service was used. Test
archive proofs are fixture proofs; these tests do not establish deployed S3,
Supabase issuer, customer membership provisioning, or learned model state.
The complete `tests/test_retention_*.py` suite passed **234 tests** with the same
disposable PostgreSQL target configured, including identity, HTTP, object-store,
and database boundary coverage.

The suite establishes:

- One atomic artifact-byte/job/outbox admission; ten concurrent same-key attempts
  admit once. Changed bytes/metadata conflict. Project-wide pending byte/count
  limits serialize across different users. Replays still work at quota.
- Owner issuer/subject plus tenant/project and active membership on every
  principal read/write. Unrelated users and scopes cannot list, search, guess
  artifact/task IDs, cancel, delete, or recall another user's memory.
- An explicit read grant needs active membership and grants no cancel/delete
  authority. Grant revocation invalidates memory references on the next read.
- Share-locked membership in a caller-owned transaction serializes revocation;
  a restricted service role cannot change active/identity/scope or self-grant.
- Failure on outbox insertion and an actual child `os._exit(24)` before outbox
  commit leave zero artifact/job/outbox rows. An actual child `os._exit(23)` after
  durable admission leaves recoverable bytes and a claimable outbox row.
- Lease expiry/reclaim fences old completion/retry. Revocation prevents an
  in-flight worker from finalizing. Digest/length/proof/version mismatch quarantines
  bytes without an availability time or watermark. Retries are bounded at eight.
- Cancellation denies artifact access, clears the live payload column, and fences
  the worker. Deletion/expiry revoke memory, clear summaries/live payloads, and
  enqueue exact-version physical cleanup only after retention expiry.
- Rejected uploads can enter `orphan_archive` cleanup evidence without becoming
  canonical or available. Exact-version purge proof and a live purge lease are
  required for physical-deletion markers. Canonical versions are not orphaned.
- The hard-crash window after object-store commit but before database reference
  commit is exercised with a real PostgreSQL fixture and stateful S3 fake:
  cancellation queues reconciliation without a version, and a fenced worker
  removes only the row-derived tenant/project/artifact key after retention. A
  second tenant's object remains intact. Missing objects are recorded as
  reconciled without claiming a physical deletion.
- An upgrade fixture starts from the original schema and pre-upgrade terminal rows,
  applies the reconciliation migration once and reruns the backfill migration to
  establish its idempotence, then recovers both an uploaded fake object and an
  already absent key exactly once. It does not invent deletion timestamps or
  enqueue still-live artifacts.
- Successive expired purge leases, malformed/forged versionless proofs, deletion
  before DB completion, and a separate-target dump/restore of expired purge and
  orphan leases are exercised. Restored leases are reclaimed under new tokens and
  both cleanup ledgers finish without touching another tenant's key.
- Memory linking needs verified artifact state and the exact digest proof supplied
  only after service readback; references explicitly do not mean model learning.
- Migration rerun, transactional rollback of schema removal, and destructive
  fixture-only reversal preserve an unrelated legacy table.

These are stateful local fixtures, not full process or S3 emulation. The worker
interruption cases leave completion steps uncalled; they do not kill and restart
an OS worker. The restore test restores database lease rows into a separate
database target while retaining the same in-memory fake object store. That
PostgreSQL integration fake still does not model paginated listings or delete
markers. The focused object-store suite now separately exercises representative
paginated `ListObjectVersions` contracts, exact-key delete-marker removal,
incomplete/repeated cursors, and post-delete absence checks; botocore `Stubber`
validates the modeled SDK response/request shapes with no network. These tests
still do not establish real bucket IAM, KMS, Object Lock, listing permissions,
S3 ordering/consistency or deployed recovery.

## Runtime and reproducible commands

Host: Windows 11 Pro build 26200, Intel Core i7-13700KF (16 cores, 24 logical
processors), 33,243,240 KiB visible memory. Python 3.12.10, SQLAlchemy 2.0.49,
asyncpg 0.29.0, PostgreSQL 17.11 x86_64 Windows (MSVC 19.44.35228). No GPU used.

The parent supplied the official EDB portable PostgreSQL archive; SHA256:
`4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf`.
Binaries and test data are outside Git, under the task's sibling `runtime`
directory. No credentials or production configuration were copied.

From this checkout, with portable binaries at
`../runtime/postgres/pgsql/bin` and the task Python environment at
`../runtime/Scripts/python.exe`:

```powershell
./scripts/retention_fixture.ps1 -Action Test
```

This creates/reuses only its explicitly marked `retention-fixture-pgdata`, binds
loopback port 55919, uses database `retention_fixture_brief09_rehearsal`, runs the
repository suite, then stops its own instance if it started it. Existing unmarked
directories are rejected; nothing is recursively deleted. `-Action Start` and
`-Action Stop` manage only that marked fixture. The script and tests need the
project's PostgreSQL Python dependencies plus pytest/pytest-asyncio.

For an already running disposable instance:

```powershell
$env:RETENTION_TEST_DSN = 'postgresql://retention_fixture@127.0.0.1:55909/retention_fixture_brief09'
$env:RETENTION_TEST_ALLOW_DISPOSABLE = '1'
../runtime/Scripts/python.exe -m pytest tests/test_retention_postgres.py -q --tb=short
```

Both explicit opt-in and a loopback database named `retention_fixture_*` are
mandatory. The fixture drops/recreates only the `retention` schema in that database.
The opt-in/name guard is a protection against accidents, not permission to repoint
the tests at live data. Never run fixture suites concurrently on one database.

The first task process used `../runtime/retention-pgdata`, port 55909, and the
`retention_fixture` role with local fixture trust authentication. Its lifetime was
handed to the parent for the combined API tests. Its exact stop command is:

```powershell
../runtime/postgres/pgsql/bin/pg_ctl.exe -D ../runtime/retention-pgdata -m fast -w stop
```

The separate scripted 55919 rehearsal was stopped successfully. Use each data
directory's `postmaster.pid`/`pg_ctl status` to identify only that fixture process.

## Measured fixture baseline

`python -m scripts.retention_fixture_benchmark` uses the same guarded environment
variables and resets its disposable retention schema. The measured run used the
separate `retention_fixture_benchmark` database on loopback 55909. It admitted and
read back 51 fixed 4,096-byte fixture payloads exactly; all admissions were pending,
with no verified archive claim. Fifty operations after the first operation form
the warm samples. Values include transaction commit acknowledgement.

| Measurement | Measured result |
| --- | ---: |
| First admission with a fresh pool | 31.404 ms |
| Warm admission p50 / p95, n=50 | 1.953 / 2.937 ms |
| Warm authorized byte-row read p50 / p95, n=50 | 0.772 / 1.061 ms |
| Serial warm admission throughput | 475.70 operations/s |
| Read after disposing/reopening the pool | 24.466 ms |
| Peak Python working-set RSS | 59,092,992 bytes |

This is one host-local baseline, not a production capacity target or improvement
claim. Database page caches were not evicted. PostgreSQL peak RSS, multi-process
load, tenant skew, WAN latency, real S3/KMS, and production data sizes were not
measured. The queryable catalog covers typed artifact metadata; it does not claim
normalized domain data or vector-based authorization.

## Dump/restore rehearsal

The benchmark's 51 committed fixtures were exported using PostgreSQL 17.11
`pg_dump --schema=retention --format=custom` and restored with `pg_restore
--exit-on-error` into a newly created, separate `retention_fixture_restore`
database. The custom dump SHA256 was
`35fda51844ad7be1044fb7584da0d855f55b51c598a50ce51ee54d936912a15f`.
Verified restored results: **51 artifacts, 208,896 payload bytes, 51 jobs, 51 outbox
rows, one membership; every payload SHA256 matched its stored digest**.

Reproduction (the target database must be new):

```powershell
$taskPg = '../runtime/postgres/pgsql/bin'
& "$taskPg/pg_dump.exe" -h 127.0.0.1 -p 55909 -U retention_fixture -d retention_fixture_benchmark --schema=retention --format=custom --file=../runtime/retention-fixture-benchmark.dump
& "$taskPg/createdb.exe" -h 127.0.0.1 -p 55909 -U retention_fixture retention_fixture_restore
& "$taskPg/pg_restore.exe" -h 127.0.0.1 -p 55909 -U retention_fixture --dbname=retention_fixture_restore --exit-on-error ../runtime/retention-fixture-benchmark.dump
& "$taskPg/psql.exe" -h 127.0.0.1 -p 55909 -U retention_fixture -d retention_fixture_restore -At -c "SELECT count(*),sum(byte_length),bool_and(encode(sha256(payload),'hex')=sha256) FROM retention.artifact; SELECT count(*) FROM retention.job; SELECT count(*) FROM retention.outbox; SELECT count(*) FROM retention.membership;"
```

This proves a local fixture database restore, not AWS backup coverage or disaster
recovery of the production MINDEX service. The dump contains fixture content only.

After the orphan-reconciliation migration, a second marked PostgreSQL 17.11
restore check seeded one 30-byte fixture artifact, dumped the `retention` schema,
and restored it into the previously unused `retention_fixture_brief09_restore_oct02`
database. Validation returned **1 artifact, 30 payload bytes, matching SHA-256, 1
job, 1 outbox row**, and confirmed `archive_reconciled_at` exists. The custom dump
SHA256 was
`e8918980ab08473eaff60dea24723915582d3ac3a165fc2c9beaf67f59768e00`. Both
databases and the dump are task-owned fixture artifacts under the local runtime;
this does not prove deployed backup or disaster recovery.

## Schema, privileges, and integration seams

`migrations/20261001_shared_retention_v1.sql`,
`migrations/20261002_private_orphan_reconciliation.sql`, and
`migrations/20261003_backfill_preupgrade_orphan_reconciliation.sql` are additive in
the new `retention` schema. The second migration adds `archive_reconciled_at` to
distinguish confirmed absence/reconciliation from verified physical deletion. The
third idempotently queues pre-upgrade deleted/cancelled versionless rows that have
not been reconciled or physically deleted; it preserves existing outbox rows and
does not fabricate completion timestamps. They touch no legacy application table.
`membership` and `access_grant`
are operator-provisioned authority; no HTTP route may create them. The runtime
login must neither own these tables nor inherit an operator/superuser role.
No RLS policy is claimed: authorization is enforced in repository queries behind
verified server-side identity; the database login must never reach browsers.

Use separately reviewed, site-specific runtime/operator roles. This example is a
privilege template, not an instruction to run against production:

```sql
GRANT USAGE ON SCHEMA retention TO reviewed_retention_runtime;
GRANT SELECT ON retention.membership, retention.access_grant TO reviewed_retention_runtime;
-- PostgreSQL row locks require UPDATE on at least one column. Only a harmless
-- audit timestamp is writable; active/issuer/subject/tenant/project stay denied.
GRANT UPDATE(updated_at) ON retention.membership TO reviewed_retention_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON retention.artifact, retention.job,
    retention.outbox, retention.memory_reference, retention.orphan_archive
    TO reviewed_retention_runtime;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA retention TO reviewed_retention_runtime;
```

The migration revokes PUBLIC schema/table/sequence access. Before rollout, inspect
role inheritance, direct grants, search paths, application connections, and actual
table ownership. The restricted fixture role test exercises admission/readback and
proves denial of membership insertion/deletion/reactivation/identity substitution
and access-grant updates. `authorize_in_session(session, principal)` requires an
already active caller transaction and holds the authoritative membership row lock
until its end. `require_membership` is a standalone fresh check. The asyncpg-native
`authorize_asyncpg(connection,principal)` shares this lock contract and requires
an active caller transaction; tests prove no-transaction and revoked-scope denial. Neither replaces
object ownership predicates in app-specific compute tables.

`get_job` returns a safe task receipt; `get` is internal and may contain private
bytes, object coordinates, and a lease, so APIs must project `public_receipt`.
`memory_list` filters scope, owner, live membership/grants, verified artifact state,
expiry, and revocation before returning any matching summary. `complete` requires
proof for the configured bucket and deterministic
`prefix/tenant_id/project_id/artifact_id` key, exact version, digest, and size.
Only a server worker supplies that proof after the object adapter verifies bytes.

## Rollout, rollback, and unresolved qualification

Keep admission/workers disabled until the issuer/membership authority, private
bucket ACL/encryption/version/Object Lock policy, KMS permissions, scoped runtime
role, and reviewed migration are approved. Apply the additive schema in the new
manual blue/green environment; rehearse auth failures, restart recovery, and exact
object readback before any separately authorized traffic switch.

Image rollback means disabling the new admission/worker and returning traffic to
the retained prior environment. **Preserve the schema and data.** No application
rollback automatically reverses PostgreSQL commits. `DROP SCHEMA retention
CASCADE` is destructive and was exercised only in the guarded disposable database;
it is not an operational rollback procedure. Retain the approved backup and
restore evidence before considering any separately approved destructive reversal.

Availability/watermark is per artifact and advances only after fenced verified
archive finalization. Source event, received, and available times remain distinct.
It is not a claim of complete time-series source coverage or normalization.

Deletion immediately denies API access and clears live payload/summary columns;
private Object Lock versions remain until retention allows exact-version cleanup.
SQL column clearing is not secure erasure of PostgreSQL pages, WAL, replicas, or
backups. Their encryption, access, vacuum/backup expiry, deletion SLA, and retained
tombstone/identity policy need operator review and deployment qualification.

`register_orphan` records known successful uploads whose finalize fence was
rejected. Terminal cleanup now queues a versionless purge as well, covering a hard
process crash after object upload but before any database reference commit. The
backfill migration makes pre-upgrade versionless tombstones claimable. The worker
derives one exact key from the tombstoned database row, enumerates versions only
under that canonical key prefix, filters to exact full-key matches, and verifies
each matching version's expected owner, version, KMS encryption, COMPLIANCE
retention, artifact metadata, full readback size, and SHA-256 before deleting any
matching version after its lock expires. It never reads or deletes prefix neighbors
or another tenant's key. The scan is bounded to 100 versions and 32 pages and fails
closed above either limit or when version listing is denied. The deployed worker
role will need `ListBucketVersions` constrained to the private artifact prefix;
that policy remains unqualified against AWS. If the exact key has no object
versions, the database records reconciliation while leaving
`physical_deleted_at` unset. A stale lease cannot commit either result. PostgreSQL
and S3 fakes qualify this local flow only; deployed S3 permissions and production
retention remain unverified. Quarantined digest mismatches must never become
available.

No production migration, cloud launch, deployment, merge, payment, ledger
transaction, or hardware operation was performed by this work.
