# 09. Build the shared tenant, retention and MYCA memory contract

**Assignment status:** Dispatched to a dedicated implementation chat on October 1, 2026. See [dispatch record](../dispatch/README.md).

**Goal:** Create the smallest reusable identity/job/artifact/evidence substrate needed by every application, avoiding per-app storage and authorization silos.

**Surfaces:** `Cross-application service contract; not a new marketing page`

Read and follow [the shared engineering contract](00-shared-engineering-contract.md). Source paths below are current review candidates, not permission to overwrite active edits.

## Evidence and current front/middle/back boundary

A robust public/non-customer source-capture slice exists with PostgreSQL receipts, fenced archival worker and verified S3 versions. It must not receive private data under a public label. Supabase preference RLS is a useful precedent, not a complete owned job/artifact system. MYCA memory tags and unsigned user headers are not authorization.

**Engine/dependencies:** Verified identity/delegation, MINDEX PostgreSQL transaction/outbox/leases, private versioned object storage and MYCA task/memory adapters.

- [CANONICAL_HISTORY_CONTRACT_OCT01_2026.md](<C:/Users/Owner1/.codex/worktrees/system-audit-repairs/CODE/mindex-release/docs/CANONICAL_HISTORY_CONTRACT_OCT01_2026.md>)
- [DURABLE_SOURCE_CAPTURE.md](<C:/Users/Owner1/.codex/worktrees/system-audit-repairs/CODE/mindex-release/docs/DURABLE_SOURCE_CAPTURE.md>)
- [source_capture.py](<C:/Users/Owner1/.codex/worktrees/system-audit-repairs/CODE/mindex-release/mindex_api/source_capture.py>)
- [source_capture_s3.py](<C:/Users/Owner1/.codex/worktrees/system-audit-repairs/CODE/mindex-release/mindex_api/source_capture_s3.py>)
- [FORMSPACE_APPLICATION_DELIVERY_PLAN_OCT01_2026.md](<C:/Users/Owner1/.codex/worktrees/system-audit-repairs/CODE/website-scientific-pages/docs/FORMSPACE_APPLICATION_DELIVERY_PLAN_OCT01_2026.md>)
- [20260126000000_user_app_state.sql](<C:/Users/Owner1/.codex/worktrees/system-audit-repairs/CODE/website-scientific-pages/supabase/migrations/20260126000000_user_app_state.sql>)

## Engineering package

1. Design additive reviewed schemas and principal claims for tenant/project, source classification, chart/dataset/job/artifact and access grants. Preserve distinct public/operator/private domains.
2. Reuse existing idempotency, byte limits, leases/fencing and S3 readback mechanisms only after private ACL/encryption/retention support is explicit. Commit job admission and outbox atomically.
3. Define task and memory receipts that link to authoritative MINDEX artifacts; exact readback is required before remembered/retained is shown. Vector similarity never grants access.
4. Publish versioned SDK/BFF contracts used by FormSpace/NLM/Earth/MYCA; no new independent data store. Add migration rehearsal and backward-compatible rollout/rollback analysis.

## Acceptance and benchmark contract

These are proposed acceptance requirements, not measured results.

- UserA cannot list/read/search/download/cancel/subscribe to UserB or another project, including cache hits and guessed IDs.
- Same idempotency key/content returns original ID; changed content409; concurrent attempts admit once and stale worker fencing cannot finalize.
- Crash between durable commit and object upload preserves recoverability; hash/version mismatch quarantines bytes and never marks verified.
- Source event time, received time and available time remain distinct; coverage watermark advances only after required committed stages.
- Revocation and deletion/retention behavior have explicit tests; no secret or private source bytes appear in public logs/ledger metadata.

## Existing tests and qualification limits

Existing public source-capture30 offline/18 database fixture checks establish only that slice. Extend with isolated real database ownership/outbox tests and fake object-store errors; no production schema mutations.

## Dependencies and ownership

Owner-approved identity issuer/membership model, reviewed additive schema and existing source-capture maintainer. This is a prerequisite gate for private app completion, not the ninth step of a serial rollout.

## First deliverable

One private dataset admission→archive verification→authorized artifact readback→MYCA memory reference, with cross-tenant failures proven.

Deliver source hashes, exact file scope, test commands/results, failure evidence, runtime limits and manual inspection links. No automatic deployment. Cursor receives a separate manual blue/green plan with immutable versions, migration safety checks, reviewed traffic switch and rollback; no production operation is authorized by this prompt.
