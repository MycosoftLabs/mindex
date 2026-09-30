# iNaturalist committed checkpoints (B5-I06 / Batch 8)

The iNaturalist taxa and observation jobs now commit each fully consumed fetched page before publishing its checkpoint. Previously they saved inside the job's uncommitted transaction and estimated page numbers from accepted-record counts. Taxa also ignored `start_page`, and the full-sync wrapper did not enable checkpointing on its first run.

## Cursor contract

- `page` is the actual last fetched, fully processed, successfully committed page. Resume starts at `page + 1`. A short page or observation lacking a taxon name cannot distort the cursor. Existing observation skip behavior is retained; a missing taxon name in the taxa job still raises and rolls back that page.
- Version 2 records include `schema_version: 2`, `committed: true`, `job_name`, a positive integer `page`, UTC `timestamp`, a `query_fingerprint`, and diagnostic metadata. `records_processed` counts accepted records in that invocation, not a lifetime count or upstream total. There is no invented `completed` flag: a capped run or empty fetch does not establish source completeness.
- The fingerprint covers effective source URL, taxon filter, page size, fixed ordering/filter arguments, and configured destination database URL. Only the combined SHA-256 fingerprint is written; raw connection strings and API tokens are not stored. The upstream token is not part of the cursor. Changing the database URL, including credentials in that URL, conservatively rejects resume. It does not prove identity if the same URL is repointed or its database is reset.
- `max_pages` remains an **absolute final page**, so extending it is allowed. For example, a committed page 10 with `max_pages=12` fetches pages 11 and 12. A start position beyond the limit fetches nothing. Page arguments must be positive integers; booleans, zero and fractions are rejected. The taxa page size is normalized to the existing maximum of 200 before fingerprinting.
- Matching checkpoints select the next page automatically. Explicit `start_page` is accepted only as the default 1 or the expected next page. Without a checkpoint, an explicit start is still the caller's deliberate choice.
- Observation metadata backfill runs after the page loop and commits in the final `db_session` exit. It has no page checkpoint; a backfill failure leaves previously committed page cursors intact and rolls back its pending changes. Its existing best-effort upstream error behavior is unchanged.

## Operating and migrating

Checkpoint files retain the existing `CHECKPOINT_DIR` default `/tmp/mindex_etl_checkpoints/<job_name>.json`. Its actual volume, permissions, retention across restarts and mount durability must be established by the operator. Nothing here activates or configures storage or starts a job. Keep one cooperating job per checkpoint file; there is no cross-process/distributed writer lock. Preserve the checkpoint and matching database together.

Use the existing Python helper with the same arguments on every resume. This is an operational example for an already configured ETL environment; it was **not run against a service** during this repair:

```python
from mindex_etl.checkpoint import resume_from_checkpoint
from mindex_etl.jobs.sync_inat_taxa import sync_inat_taxa

count = resume_from_checkpoint(
    "inat_taxa", sync_inat_taxa, per_page=100, max_pages=12, domain_mode="fungi"
)
```

For observations, supply a fixed `updated_since` value when using a time filter. A newly computed `lookback_hours` window has a different effective query at each invocation and its previous cursor is rejected. Backfill settings and the page limit do not change page identity. The `scripts/full_fungi_sync_v2.py` iNaturalist wrapper now uses this helper on its first run and all resumptions. Other jobs in that composition script are outside this change; the entire script was not executed.

Legacy files cannot prove their page was committed, even if they have `completed: true`. Corrupt, legacy, mismatched-job, malformed or different-query files raise before fetch or database writes. There is deliberately no automatic conversion. Stop the job, retain the old file for diagnosis, then explicitly archive/remove that one job's cursor and restart from page 1 with matching intended arguments. Do not copy the legacy page number into version 2. Replaying existing records relies on the existing upsert behavior, not an exactly-once guarantee. Checkpoint files are local operator state, not authenticated or tamper-proof records.

Publication serializes JSON before touching the old file, writes a unique sibling temporary file, flushes and fsyncs it, then uses `os.replace`. Ordinary serialization/write/replace failures retain the previous cursor when replacement has not happened, and propagate to the caller. A page whose database commit succeeds but checkpoint save fails is replayed. An ambiguous commit error likewise leaves the older cursor. Atomic file replacement and file fsync do not establish directory-entry durability under a whole-machine power failure.

## Offline verification

The new fixture loads selected actual source modules, including `db_session`, with fake upstream and configuration modules. It uses real temporary SQLite commits and independent connections to check durable visibility; a narrow adapter maps observation writes into a fixture table. This verifies transaction/checkpoint ordering, **not PostgreSQL/PostGIS SQL semantics**, live iNaturalist behavior or full ETL startup. The full-sync wrapper alone is AST-extracted to avoid importing unrelated jobs. No production credentials are loaded by the inspected fixtures.

From this repository with its test dependencies installed, an equivalent bounded invocation is:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest --noconftest -p no:cacheprovider -o addopts= -q tests/test_batch8_inat_checkpoint_contract.py tests/test_batch5_inat_ingestion_contract.py
```

The audit additionally used an external runner that blocked selected socket/process audit events and named credential basenames `.env`, `.env.*`, `agent.env`, `.credentials.local`. It is not a general OS sandbox. Final selected result: **83 passed** (47 new checkpoint cases and 36 unchanged ingestion regressions), one pytest warning for the existing `asyncio_mode` option with plugin autoload disabled. The first authoritative pre-fix run of the initial 35 new plus 36 prior cases was 32 failed / 39 passed. A later database-target-binding check separately reproduced two failures before its fix. Finally, the same 47 new cases run against the immutable effective source baseline produced **43 failed / 4 passed**; the unchanged `db_session` source was shared by both runs. Baseline replay changes no source and is distinct from the initial pre-edit receipt.

## Remaining limits and rollback

Source offset pages are ordered by mutable observation counts or observation dates. There is no inspected snapshot/cursor token, so upstream insertions/reordering can still cause omission or replay between requests/runs. This repair prevents local commit/checkpoint misordering, not those upstream consistency problems. At-least-once replay is conditional on retained checkpoint/database state and stable enough source pagination; exactly-once delivery and global ingestion completeness are not claimed. Local scrape archival remains best-effort and is not covered by the database checkpoint.

Rollback only the Batch 8 source/docs/test change, preserving prior ingestion repairs. Stop the affected job and archive its version 2 cursor before running old code: the old loader ignores the new proof fields and interprets page numbers incorrectly. Restart old code from page 1 if rollback is required; its pre-existing checkpoint defects return. No database migration is required and rollback does not undo already committed pages. No source was deployed, no original checkout was changed, and no live ingestion was performed by this repair.
