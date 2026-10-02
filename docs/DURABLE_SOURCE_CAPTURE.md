# Durable capture of public-source observations

This opt-in service retains exact submitted source bytes in PostgreSQL, retries private S3 archiving, and records the verified object version and SHA-256 in MINDEX. It is disabled by default. A database acceptance receipt is **pending**, not proof that the cloud archive exists. No website/device collector is automatically connected by this change.

Only explicitly allowed public, non-customer sources belong in this initial boundary. Internal service authentication is required for capture, status and retrieval. Shared service tokens do not isolate customers or prove an upstream source's identity. This API must not be exposed to browsers or the public Worldview API. A source allowlist is an operational admission control, not automatic detection of private data or proof of licensing.

## Routes and evidence

Paths below use the default internal prefix `/api/mindex/internal`:

| Method/path | Contract |
| --- | --- |
| `POST /source-captures/{source_id}` | Raw body, `Idempotency-Key`, allowlisted `Content-Type`, optional `Content-Encoding: identity` or `gzip`, optional timezone-aware `X-Source-Observed-At`. Authenticate with the existing internal token convention. No caller-selected URL, bucket, path or upstream-header map. |
| `GET /source-captures/{capture_id}` | Private state/checksum receipt. No schema setup, writes or cloud call. |
| `GET /source-captures/{capture_id}/raw` | Download the committed pending bytes or exact indexed S3 version. Verify SHA-256 and byte length before returning. Private/no-store, attachment, octet-stream; no upstream compression instruction or executable media type. |

Accepted input media types are `application/json`, `application/geo+json`, `text/csv`, `text/plain` and `application/octet-stream`. The bytes are those supplied by the collector; the API does not parse/re-serialize them. They are not certified network-packet bytes or scientifically validated observations. Server capture time, source-reported observation time and cloud verification time are distinct; absent source time stays null. No freshness score is inferred.

Idempotency is scoped by authenticated service, exact canonical lowercase source ID and key. Same key plus identical payload and normalized metadata returns the original capture; changed bytes or metadata return409. Different idempotency keys preserve distinct observations even when their content hashes match. Shared-token callers use the shared `internal` service namespace; select stable unique observation keys within that namespace.

| State/result | Meaning |
| --- | --- |
| HTTP202 `pending_archive` / `archiving` | PostgreSQL committed the receipt and pending bytes; cloud verification is false. A duplicate pending request also returns202. |
| `archived_verified` | Worker retrieved the exact S3 version and checked length/SHA-256, configured KMS encryption/key and capture identity, then committed its MINDEX reference. Pending database bytes are released only in that final transaction. An archived duplicate POST returns200. |
| `integrity_blocked` | Verification failed. Pending bytes are retained and no automatic retry can erase/replace the conflicting evidence. An operator must investigate before a narrowly controlled retry; this release has no public reset/delete endpoint. |
| HTTP409 | Idempotency conflict; no overwrite. |
| HTTP413/415/422 | Invalid size, media/encoding or metadata/source. |
| HTTP503 | Disabled/unconfigured, capacity exhausted, schema/database/object-store unavailable, or integrity failure. Messages are sanitized. No empty-body success or cloud-durability claim. |

## Explicit setup and configuration

Apply only `migrations/20260930_source_capture_retention.sql` through the operator's reviewed migration process, to the intended database. This adds the `raw_source` schema, capture table, constraints and retry index in one transaction. Routes, startup and the worker never apply it. If a distinct migration owner applies it, grant the API/worker role only the required schema usage and SELECT/INSERT/UPDATE permissions; do not grant public access. Existing tables and data are not rewritten.

Install the optional storage dependency with the project's managed environment (`.[storage]`). The S3 adapter is lazy; missing storage dependencies make cloud processing unavailable. Qualification used boto3 1.43.30; the declared range supports current conditional uploads. Configure through environment/secret management, never committed credentials:

| Variable | Default / requirement |
| --- | --- |
| `SOURCE_CAPTURE_ENABLED` | false; explicit opt-in |
| `SOURCE_CAPTURE_SOURCES` | empty; comma-separated approved lowercase source IDs |
| `SOURCE_CAPTURE_MAX_PAYLOAD_BYTES` | 8MiB, hard configuration ceiling16MiB |
| `SOURCE_CAPTURE_MAX_PENDING_BYTES` | 256MiB, ceiling1GiB, at least per-payload maximum |
| `SOURCE_CAPTURE_MAX_PENDING_COUNT` | 1000, ceiling100000 |
| `SOURCE_CAPTURE_LEASE_SECONDS` | 120, range30–900 |
| `SOURCE_CAPTURE_BUCKET` | required for archiving/retrieving archived objects; explicitly selected target |
| `SOURCE_CAPTURE_PREFIX` | `source-captures`; use a dedicated private prefix |
| `SOURCE_CAPTURE_KMS_KEY` | exact KMS key ARN, not an alias; matches returned object evidence |
| `SOURCE_CAPTURE_EXPECTED_OWNER` | required bucket-owner account ID; private deployment configuration |
| `SOURCE_CAPTURE_REGION` | required AWS region |

Existing internal credentials and MINDEX DB configuration remain in their established secret/configuration mechanisms. The worker uses the AWS SDK credential chain, preferably an assigned workload role. It does not copy application secrets into rows or S3 metadata. No bucket, policy, key, scheduler or IAM principal is provisioned by this package.

The operator must establish least-privilege prefix/key permissions, block public access, enforce TLS/KMS, enable versioning, and supply the required read/decrypt permissions for verification. Code checks do not prove the entire account policy denies every other principal public access. Default bucket encryption alone is not a substitute for a qualified writer policy. Changing the configured bucket/prefix/key can intentionally make old references unavailable; retain matching configuration or use a separately reviewed migration, not a silent fallback.

## Worker, leases and failures

Invoke an explicit bounded pass:

```sh
python -m mindex_etl.jobs.archive_source_captures --limit 10
```

The maximum is100 attempts per invocation. No scheduler or service is installed automatically. Persisted `FOR UPDATE SKIP LOCKED` leases coordinate workers. Expired work can be reclaimed; a lease token and expiry fence late completion/retry writes. The worker performs S3 I/O outside the DB claim transaction and uses SDK timeouts/retries. It reports attempted work, not an invented completed-object count. SDK/network/filesystem behavior is not an absolute wall-clock guarantee.

An archive uses a key derived from capture ID and SHA-256, expected bucket owner, explicit KMS key, SHA-256 upload checksum and `If-None-Match: *`. A412 after an ambiguous upload causes exact-version retrieval and verification. Other cloud errors retain pending bytes with a sanitized category and persisted exponential retry delay capped at one hour. A failed final DB commit leaves the old lease/payload; retry after expiry reconciles the existing object. No distributed transaction, exactly-once delivery or overwrite/deletion guarantee against separately authorized external actors is claimed.

The pending-byte/count budget is enforced under a PostgreSQL transaction advisory lock across concurrent admissions. It includes blocked records that still retain bytes. **It is not a cap on the physical database**, its indexes, TOAST, WAL, backups, dead tuples or retained metadata. A fixture holding8 payload bytes occupied65536 relation bytes including indexes. Archived index rows remain and grow with distinct observations. Operators need separate volume quotas, alerts, vacuum/backup capacity and a reviewed retention policy. There is no automatic archive/index deletion in this slice.

## Validation and reproducibility

The focused unit suite uses real selected modules, the actual internal auth dependency, fixture configuration and in-process HTTP/object transports. It does not load the complete application or read deployment credentials. Run separately from unrelated suites:

```sh
python -m pytest --noconftest -q -o addopts='' tests/test_source_capture_contract.py
```

Disable third-party pytest plugin autoload for a fully controlled environment. The repository's `asyncio_mode` setting can then produce an unknown-option warning; these tests use explicit asyncio runners. The suite does not call AWS or a database.

`tests/test_source_capture_postgres.py` is an explicit standalone integration runner requiring `RETENTION_TEST_ISOLATED=1` and `RETENTION_TEST_DSN`. Its guard accepts only a synthetic `retention_fixture` user, host `retention-db`, database `retention_fixture` (or `retention_restore` for restore verification), and the psycopg SQLAlchemy dialect. It applies the new migration, truncates its fixture capture table, and starts owned child processes that exit abruptly. Use only a disposable internal-network PostgreSQL16/PostGIS3.4 test environment, never a production DSN. Fixture credentials belong in that test runtime, not the repository.

```sh
python -B tests/test_source_capture_postgres.py
```

Qualification: 30 offline unit cases passed; 18 named real-PostgreSQL checks passed, covering atomic admission, concurrent idempotency/capacity, distinct observations, lease fencing, actual child-process crashes before/after commit, persisted cloud retry, post-upload verification, failed final commit and missing schema. Two extreme timezone-conversion cases initially reproduced an overflow and now return the intended invalid-observation422 result. The object store is explicitly synthetic. The SQL suite also produces a synthetic object export for restore qualification; it is not captured customer/source data.

A separate real `pg_dump -Fc` of the fixture's `raw_source` schema was restored with `pg_restore --exit-on-error` into a freshly created `retention_restore` database. The runner's `--verify-restored` mode recovered one pending payload and retrieved one indexed archived version from the synthetic object export, validating both checksums. This proves the fixture schema/bytes/index survive PostgreSQL dump/restore; it does not establish production backup freshness, recovery time, S3 permissions or disaster recovery.

## Producer integration, rollout and rollback

A producer must capture bytes before normalization, reuse a stable idempotency key for retry, and distinguish database-pending from cloud-verified state. When the capture API is down, fire-and-forget HTTP is insufficient: a producer needs durable local retry storage or must report retention unavailable. Existing website feed/CREP helpers are not automatically changed. Attaching a capture ID to normalized map/search records is a subsequent schema/consumer integration; this raw index does not materialize every observation into those product schemas.

Before production use: independently review the source; qualify the worker IAM/bucket/prefix/key; perform a synthetic cloud upload/readback and denied-access checks; restart with pending work; restore the DB and version manifest to a separate environment; then enable one authorized public source and verify producer receipts. Measure pending age/count/bytes, integrity blocks, retry/error categories, storage growth and cost before scaling. Local pending data is only as durable as its PostgreSQL volume/backup. No cloud RPO-zero or unmeasured RTO promise applies to pending captures.

To roll back, stop new admissions and the worker, preserve pending rows and object versions, then disable the feature/revert only this code. Do not drop the new schema or delete objects as rollback. Older code ignores the new tables but cannot drain the queue. Inspect pending/integrity-blocked records and retain recovery configuration before changing storage targets.

## External service contract references

The [S3 PutObject API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html) documents version IDs, encryption/checksum fields and expected-owner checks. [Conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html) define conflict behavior. [Upload integrity](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html) explains checksum support; this implementation also recomputes SHA-256 on returned bytes rather than treating ETag as a universal digest. These references inform the adapter contract, not a claim that deployed policies are already qualified.
