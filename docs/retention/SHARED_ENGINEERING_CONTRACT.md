# Shared engineering contract — read before any application brief

These are discussion/assignment prompts requested by the owner. Saving them does not dispatch work or authorize live operations. Revalidate source hashes and assign disjoint paths before implementation; other agents have active changes. Preserve approved glass design, real product marks, scientific artwork, existing videos/photos and source attribution. A theme/animation change does not repair an engine.

## Required architecture

MINDEX owns authoritative retained history, immutable dataset/run/artifact evidence and provenance for all apps. Historical queries must use internal records only, with requested/available time range, source coverage, watermark and explicit missing/unavailable states. Fresh live display may obtain external data for latency, but every response/event needs durable capture acknowledgement and eventual linked normalization; an unawaited promise is not retention. A raw blob, normalized record, searchable catalog entry, verified archive and learned model state are separate lifecycle stages.

Supabase supplies verified user identity and server-derived membership/preferences. Bind every private dataset, job, artifact, cache, memory query, download and event stream to `(issuer, subject, tenant/project)`. Never trust browser owner fields or unsigned forwarded user IDs. Test two unrelated users and two projects, invalid/expired tokens and direct API requests. Internal operators have separate policies from authenticated WorldView customers. Service keys alone do not establish tenant authorization.

MYCA orchestrates authorized typed operations with durable task/run IDs and exact result/evidence readback. Model assistant text is never user identity, authorization or a tool command. Private records remain offchain; proof hashes still require privacy review. Local ledger markers, queued UI state and API200 are not independently confirmed blockchain transactions, successful jobs or authenticated model outputs.

AWS primary is intended architecture, not a completed cutover. A retained encrypted/versioned backup is not a restored database, running app or tested fallback. Keep an explicit coverage/readiness contract. No new data silo; reuse reviewed existing adapters after their ownership contracts are adequate.

## Standard engineering package

1. Snapshot exact repository HEAD, dirty allowlist, source hashes, relevant migrations and prior test receipts. Separate imported/pre-existing code from the delta. Agree owner/write paths before touching shared files.
2. Produce a front/middle/back contract map: UI action → authenticated request → validated domain operation → engine → durable receipt → authorized readback. Mark every absent dependency and avoid success-shaped fallback responses.
3. Repair the smallest meaningful vertical slice with versioned request/response schemas, idempotency and bounded input/output. Failure states include unauthorized, invalid, unavailable, pending, partial and failed; empty valid data is distinct.
4. Add tests at actual boundaries, including cross-tenant denial, replay/conflict, malformed data, timeout/cancellation, process restart where persistence is promised, and source-preserving failure. Fixture providers must be explicitly labeled. Do not test only an implementation mirror.
5. Record benchmark hardware/software/data size, warm/cold runs, p50/p95 latency, peak RSS/GPU memory when relevant, throughput, correctness and failure coverage. Proposed acceptance targets must be labeled targets. Establish baseline before promising performance improvements; report missing measurements.
6. Deliver a patch/file hash manifest, migration/reversal plan, reproducible test commands/results, source/engine limitations, manual inspection checklist and deployment/rollback handoff. Do not claim ready from compile/HTTP200 alone.

## Release boundary

No automatic deploy, workflow dispatch, cloud launch, production database change, ledger transaction, public release, credential rotation, package purchase or actuator operation. Cursor owns a separately reviewed **manual blue/green** rollout using immutable image/source/artifact versions, health/data/auth smoke checks, explicit traffic switch and rollback to retained prior versions. A migration must be backward-compatible or have a separately reviewed migration/recovery window; rolling back an image does not undo data changes. Keep the previous environment until the authorized acceptance gate passes.
