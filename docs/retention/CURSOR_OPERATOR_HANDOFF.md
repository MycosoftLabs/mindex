# Cursor operator handoff: retention issuer, membership and archive

Updated 2026-10-03. This handoff is for Cursor to prepare and run an approved
nonproduction integration qualification. The local adapter patch and tests do not
authorize production configuration, migration, deployment or data operations.

## Source and local evidence

The scoped change is in `mindex_api/retention/object_store.py` and
`tests/test_retention_object_store.py`. It includes pagination and exact-key
delete-marker reconciliation, with fixture and botocore Stubber coverage. The
existing retention v1 HTTP, repository, identity and archive proof shapes remain
unchanged. No schema or migration changed. Run the bounded local check from the
MINDEX checkout:

```powershell
python -m pytest tests/test_retention_object_store.py -q
git diff --check -- mindex_api/retention/object_store.py tests/test_retention_object_store.py docs/retention/ARCHIVE.md docs/retention/POSTGRES_QUALIFICATION.md docs/retention/CURSOR_OPERATOR_HANDOFF.md
```

The tests are offline contract fixtures; they are not real AWS evidence. The
Stubber uses the installed botocore model and blocks network access.
An independent review of original patch commit `fc2f8ca6d0eb2f433d95c1034d4ee7343c2732d0`
reported no concrete blocker in `outputs/brief09-delete-marker-review-oct03.md`
(SHA256 `ec1ef2869f55b549ac7126192a43807321718406055a912d919c3940bab33fd6`).
That review corroborated the local 93-test report; it did not rerun tests or
contact AWS.

## Operator-supplied identity and membership values

Before configuring a nonproduction runtime, obtain the exact Supabase project
issuer and audience from the identity owner. This implementation expects the
project's fixed HTTPS issuer (normally
`https://<approved-project>.supabase.co/auth/v1`), a single-string audience
(`authenticated` for the described Supabase user token), and the same-origin
JWKS URL (`<issuer>/.well-known/jwks.json`). Confirm the signed user token has
the required `sub`, `exp`, `iat`, `role=authenticated`, and
`is_anonymous=false` claims. Do not derive issuer, keys, role, subject, tenant,
project or membership from browser headers or user metadata.

The MINDEX membership owner must supply the authoritative provisioning and
revocation process for exact `(issuer, subject, tenant_id, project_id)` tuples.
Validate allowed and denied access for two unrelated users across two projects,
including a guessed artifact ID, token refresh/key rotation, cached-key expiry,
membership revocation during a read, and revocation before purge. Membership is
checked in MINDEX for every operation and is not inferred from a valid JWT. Keep
the runtime API role unable to edit membership identity, scope or active state;
the schema/membership owner provisions and revokes rows through the approved
administrative path.

## Nonproduction archive qualification

The storage owner supplies a dedicated private test bucket, AWS account ID,
region and full KMS key ARN. Independently verify enabled bucket versioning,
four Public Access Block flags, `BucketOwnerEnforced`, Object Lock enabled, and
effective least-privilege IAM. The worker needs exact-key object read/write and
version-specific deletion plus `s3:ListBucketVersions`; confirm KMS permissions
for the chosen key and expected-bucket-owner checks. Do not grant retention
bypass. Use synthetic bytes and an isolated expired fixture only.

Exercise successful put/readback of the exact version, checksum/metadata/lock
verification, denied list/get/delete permissions, ambiguous upload response,
multi-page inventory containing both data versions and delete markers, marker-
only inventory, repeated/missing pagination cursors, delete acknowledgement with
the item still listed, and process interruption after upload but before the
database reference commit. Confirm recovery deletes only the canonical
tenant/project/artifact key, proves the complete version/marker inventory empty,
and lets the repository complete only under the current lease. Include Object
Lock expiry and legal-hold rejection. Record account, region, bucket, KMS key
identifier, source commit, sanitized request IDs, test row IDs, observed pages,
versions/markers removed, and start/end times; never copy credentials or private
payloads into the handoff.

AWS `ListObjectVersions` pagination uses `NextKeyMarker` and
`NextVersionIdMarker`; a delete marker is physically removed only with a
version-pinned `DeleteObject`. `HeadObject` on a specified delete-marker version
returns 405, so reconciliation verifies the final full inventory rather than
using HEAD as a marker probe. See [ListObjectVersions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectVersions.html),
[HeadObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html),
and [Managing delete markers](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ManagingDelMarkers.html).

## Rollback and recovery gates

Before any separately approved rollout, rehearse the additive migration and
backup restore in a disposable target. Snapshot schema metadata, pending payload
rows, archive references and a private-bucket version inventory. Keep old and
new immutable application images available. If qualification fails, disable
retention admissions and workers, restore the previous application binaries,
and keep the additive schema, tombstones, queues, KMS configuration and locked
object versions intact. Do not drop the schema or claim that code rollback
reverses retained data. Reconcile interrupted uploads and purges under new
leases, then verify restored bytes and references in a separate approved target
before measuring RPO/RTO.

Unresolved operator gates are the owner-approved issuer/audience, active
membership source and lifecycle, database least-privilege role grants, effective
AWS account IAM/KMS/bucket policy, real Object Lock/version-marker recovery,
backup/restore acceptance, and application integration. Cursor owns any later
deployment; no deployment or migration is part of this handoff.

## GitHub publication review

Fresh GitHub API inspection on October 3, 2026 found `main` still at
`42b876fcfca2e86b0365e8fe8afab628d6a94705`. The sanitized local review branch
is `codex/retention-s3-delete-markers-review-oct03`; it starts at that exact
commit and contains the retention v1 implementation, its migration and SDK
dependencies, recovery fixes, qualification notes, and this S3 reconciliation
fix. It excludes the unrelated catalog/devtools commit chain.

The current repository has no configured webhooks and `main` is not branch
protected. `Deploy MINDEX to VM 189` is configured only for matching `main`
pushes or manual dispatch; do not push to `main` or dispatch it. A pull request
runs `platform-one-build`. The latest observed PR run passed tests and skipped
Iron Bank authentication/build/push; repository-level secrets did not list those
inputs. Its failure fallback can call the external agent API. Organization-level
secrets/variables and GitHub App installation policy could not be inspected
with the available account (403/401), so this audit does not establish a fully
safe PR or branch-push route. No branch was pushed and no PR was opened.

Before Cursor publishes this branch, a repository/org owner must verify there
are no inherited `IRON_BANK_*` image-publish inputs or other push/PR automation
that can publish/deploy, and confirm how to prevent or accept the agentic
failure fallback. Then recheck the workflow files and installation/webhook
policy immediately before push. Keep any PR draft; do not merge, dispatch a
workflow, apply a migration, or deploy as part of this qualification handoff.
