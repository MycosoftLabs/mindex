# Private retained object adapter (retention.v1)

This adapter is part of brief 09's MINDEX retention service. It does not reuse
the public source-capture classification or import its adapter. Reference review:
`mindex_api/source_capture_s3.py` in the isolated audit source had SHA-256
`73b7ca47413eb9c85fd5f06f7cc6be84c0b50b6731fc35916c78405bfdba11e0`.
Implementation baseline: MINDEX commit
`42b876fcfca2e86b0365e8fe8afab628d6a94705` on
`codex/brief09-shared-retention`. The baseline's unrelated UTF-8 test-output
changes are outside this adapter's ownership.

## Integration boundary

`create_object_store(config)` creates an SDK client only when called explicitly.
Importing the module creates no client and performs no credential resolution or
network I/O. Tests can inject a client with `PrivateObjectStore(client, config)`.

The caller must obtain the row through current MINDEX membership authorization
and enforce deletion/revocation before every read. This low-level adapter has no
identity issuer, does not grant access, and is not an HTTP/download endpoint.
It creates no signed download links. A cached object reference, vector match,
service key, or guessed UUID cannot substitute for that service authorization.
Do not return storage coordinates to customers or pass browser-provided rows.

Persisted rows supply canonical UUID `tenant_id`, `project_id`, `artifact_id`,
`classification="private"`, lowercase SHA-256 `sha256`, bounded `byte_length`,
and aware UTC `retention_until`. Archival also requires committed `payload`
bytes. Reads/purges use `object_bucket`, `object_key`, `object_version` from the
authoritative receipt. Job IDs remain a MINDEX concern.

| Operation | Successful result | Required caller behavior |
| --- | --- | --- |
| `archive(row)` | `bucket`, `key`, `version`, `verified=true`, `sha256`, `byte_length` | Fenced transaction records verification; never mark verified merely on upload acknowledgement |
| `read(row, reference=None)` | Exact original bytes | Reauthorize membership and revocation; do not infer ownership from the object key |
| `delete(row)` | Exact `bucket`, `key`, `version`, `deleted=true` | Revoke customer access first; claim/fence the purge job and record the receipt |
| `reconcile_delete(row)` | Versionless reconciliation proof for the row-derived exact key | Worker-only recovery for a tombstone whose uploaded version was never committed to PostgreSQL; it is not the persisted-version delete path |

The key is `<configured-prefix>/<tenant-uuid>/<project-uuid>/<artifact-uuid>`.
Its immutable metadata allowlist contains only `artifact-id` and `sha256`.
Subjects, emails, tokens, labels and raw source bytes are absent from metadata.
UUIDs and digests remain sensitive linkage identifiers: keep bucket access logs,
receipts and application errors private; they are not safe public-ledger proofs.
This module emits no logs. SDK exception text is replaced with fixed error codes
and its displayed traceback context is suppressed.

## Required AWS protections

The operator must provision and independently review the dedicated private
bucket, account, region and full KMS key ARN. The adapter does not modify bucket
configuration. Before archival/read/purge it requires enabled versioning,
all four bucket Public Access Block flags, `BucketOwnerEnforced` ownership,
and enabled Object Lock. Every applicable call sends `ExpectedBucketOwner`.
The KMS ARN must identify a key in the configured region/account; aliases are
rejected. Configured endpoint overrides are ignored by the production factory.

Uploads use conditional `IfNoneMatch="*"`, explicit SHA-256 checksum and
`aws:kms` encryption, plus `COMPLIANCE` Object Lock and the persisted deadline.
The deadline is rounded upwards by less than one second for S3 timestamp
precision. The bucket default is not treated as proof of an individual version's
retention. The explicit request uses the documented
[PutObject retention and checksum parameters](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).

Readback pins `VersionId`, verifies version, full size, encryption/key, checksum,
metadata, actual SHA-256 bytes and compliance retention through the requested
deadline. Missing retention/encryption permissions fail closed. Reads are bounded
by the admission limit, use chunks of at most 64 KiB, detect short/extra bytes,
and close the stream on success and all failure paths. Headers alone never prove
successful archival. Integrity/retention failures must be quarantined by the
worker and must not mark a job verified or its watermark available.

If an upload times out after committing, the adapter resolves the current
immutable key and then verifies that exact version. A confirmed missing key
permits one conditional retry. Access-denied errors do not imply absence or
permit a retry. A conflicting object fails readback rather than being replaced.
MINDEX leases/fencing, pending-byte budgets and retry scheduling remain owned by
the repository/worker; this adapter adds no independent queue or datastore.

Required IAM capabilities include bucket versioning/public-block/ownership/lock
inspection; `s3:PutObject`, `s3:GetObject`, `s3:GetObjectVersion`,
`s3:PutObjectRetention`, `s3:GetObjectRetention`, `s3:GetObjectLegalHold`; and
the scoped KMS encrypt/decrypt/data-key permissions needed by upload/checksum
readback. Exact-object absence checks require suitable `s3:ListBucket` permission:
a 403 is never interpreted as a missing object. Versionless orphan reconciliation
also requires `s3:ListBucketVersions`, scoped by the bucket policy to the private
artifact prefix; denied or malformed version inventory fails closed. A separately
scoped purge role needs `s3:DeleteObjectVersion`. Grant no governance bypass or
bucket mutation rights.
Review actual effective IAM, bucket/access-point and KMS policies separately;
bucket protection flags do not prove least privilege for every authenticated
principal. S3 permissions must restrict reads/writes to the intended workers and
prevent unreviewed retention/policy changes.

## Revocation, retention and deletion

Customer revocation is an immediate MINDEX authorization decision. It must stop
downloads, memory dereferencing, searches and streams even while immutable bytes
remain retained. Do not describe revocation as physical erasure.

The purge worker waits until the persisted retention deadline. The persisted-
version delete path checks that exact version's actual deadline and legal hold,
rejecting continued retention with `archive_retention_active` (409). It deletes
only that recorded version and never requests governance bypass, creates delete
markers, or shortens a lock. Separately, versionless orphan reconciliation lists
versions under the one canonical key prefix, filters to exact full-key matches,
and verifies every match's metadata, retention, full bytes and digest before it
deletes any of those exact-key versions. Prefix neighbors and other keys are not
read or deleted. S3 compliance retention protects each version until expiry, as
documented in [Object Lock behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html).

After deleting the exact version, another version-specific HEAD must prove it
absent. Acknowledgement alone produces no deletion receipt. An ambiguous delete
is reconciled through that same absence check; an already absent version is
idempotently acknowledged after policy/deadline checks. Actual retention may be
extended or a legal hold added; the purge outbox must remain pending and retry
without restoring user access. Replicas, backups, exports and another object's
versions are outside this receipt and require their own deletion inventory.

## Limits, reproduction and qualification gates

Admission limits come from `RetentionConfig.admission()` (default payload 8 MiB,
hard cap 16 MiB). SDK connect timeout is 5 seconds, read timeout 15 seconds,
standard retry budget two total attempts per call, pool size four; application
upload retry is bounded to two conditional writes. Socket timeouts are not a
whole-operation deadline: multiple policy checks, streaming chunks and retries
can outlast a default worker lease under failures. Fencing must reject stale
completion, and deployment qualification must measure or renew leases safely.
Do not launch parallel unbounded clients to evade these limits.

Reproduce locally without AWS credentials or network requests:

```powershell
python -m pytest tests/test_retention_object_store.py -q
git diff --check -- mindex_api/retention/object_store.py tests/test_retention_object_store.py docs/retention/ARCHIVE.md
```

The stateful fake tests exercise policy/permission denial, configuration/path
tampering, cross-project reference rejection, KMS/version/hash/size/metadata/lock
mismatch, bounded reads/stream closure, ambiguous writes, concurrent-key replay,
retention/legal-hold deferral, exact-version deletion and error sanitization.
The optional real boto3 Stubber test validates SDK request/response models while
preventing network access. These are offline adapter receipts, not end-to-end
AWS security, restore, production retention or erase qualification.

The focused adapter suite also supplies AWS-shaped paginated `ListObjectVersions`
responses with both `Versions` and `DeleteMarkers`; an installed botocore
`Stubber` validates the modeled request and response shapes without network I/O.
The reconciler follows both markers, deletes exact-key payload versions and marker
versions by ID, then requires a fresh empty inventory before returning proof. A
marker-only key is reported absent only after the marker disappears. The scripted
fixtures do not establish actual S3 ordering, permissions, strong-consistency
behavior, IAM/KMS/Object Lock policy or deployed recovery. The PostgreSQL dump/restore
test restores database lease state into a separate target while reusing the same
in-memory fake object store. Worker interruption cases simulate failure windows;
they do not terminate and restart an OS worker process.

Before a manually authorized rollout, validate real bucket protections and
effective IAM/KMS policies; use an approved isolated bucket to exercise encrypted
version readback, missing permissions, Object Lock rejection, legal hold and
post-expiry purge; run MINDEX two-user/two-project authorization/outbox/fencing
checks and MYCA exact memory dereferencing; measure timing/resource limits and
recovery after process interruption. Confirm retention policy ownership and
customer erasure obligations before enabling compliance retention. No AWS calls,
bucket provisioning, production migrations or deployments were performed by
these tests. Reverting code does not remove already locked objects; retain the
previous runtime and additive schema during the manual rollback window.

Focused object-store follow-up on October 3, 2026: **93 passed** with
`python -m pytest tests/test_retention_object_store.py -q`; selected-file
`git diff --check` passed. This includes offline pagination/delete-marker tests
and botocore Stubber model validation, not live S3 qualification.
