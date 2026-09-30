# MINDEX audit repair release — September 30, 2026

This release repairs data ingestion, search and snapshot contracts that could skip records, reuse results for different filters, report failed writes as successful, or label corrupt device frames valid. It combines seven independently reviewed repair packages on upstream `b1137e9ba7eeabacbbd4f28a2e022dbeffbf3a2e` for `codex/audit-ingestion-and-data-contracts`. Publication and deployment are separate steps; this document records source qualification, not evidence that a VM has been updated.

## Changes and practical effect

| Finding | Problem and resulting behavior | Remaining boundary |
|---|---|---|
| M-01 | Worldview search used the wrong internal argument names and response shape. The adapter now forwards scalar defaults, flattens permitted domain buckets, excludes private domains and caps total results. An entirely unsupported domain selection returns 400. | Authentication, entitlement and live schema compatibility still require integrated qualification. |
| M-02 | PostgreSQL shorthand casts confused SQLAlchemy bind parameter parsing. Metering now uses explicit `CAST(:parameter AS type)` for UUID, inet and JSONB. | Tests compile real asyncpg parameters; they do not execute customer billing transactions. Background metering remains best effort. |
| B5-I01 / I02 / I03 | iNaturalist pagination could omit its final page, valid zero coordinates were lost, and updates retained an old observation time. The producer now retains the final page, zero latitude/longitude and corrected event time. | Existing data is not automatically backfilled; the separate map mirror can still retain old records. |
| M-07 / M-08 | Search cache identity omitted filters, and database failures became empty success. Cache identity now includes all accepted options. Selected queries use savepoints sequentially on one session; a failed selected domain yields sanitized, explicitly incomplete 503 responses before success side effects. | Healthy searches still have acquisition/persistence side effects. Cache separation does not implement filters ignored by domain SQL or establish tenant isolation. M-08 remains partial. |
| B5-R01 | Rolling back one bulk row could undo earlier rows while returning their success counts. Each row now has its own savepoint and counts reflect committed successes; an outer commit failure returns 503. | PostgreSQL/PostGIS execution, ambiguous commit acknowledgements and caller retry/idempotency remain to be qualified. |
| B5-R02 / R03 / R05 | Snapshot reads could ignore a supplied region, perform schema setup and imply unsupported freshness. Unsupported regional selection now returns 422; reads issue SELECT only; unavailable storage is distinct from missing data. Public metadata preserves local snapshot identity, exposes a minimal allowlist and reports freshness as unknown. | Regional matching and source-specific freshness assessment are not implemented. Governance scores and capture timestamps do not establish scientific accuracy. |
| B7-M01 / M02 | Parseable frames with bad CRCs and malformed COBS could be reported valid. Validity now requires CRC integrity; literal zero bytes in COBS data blocks are rejected. Diagnostic parsed bytes remain available. | CRC is not authentication. MINDEX's seven-byte big-endian dialect remains incompatible with the inspected sixteen-byte MAS/MycoBrain dialect. No converter or device command was introduced. |
| B5-I06 | Checkpoints could advance before database commit and used inferred rather than fetched page numbers. Each fully consumed page now commits before an atomic versioned checkpoint is published; taxa resume and first-run checkpoint setup are honored. | See [the checkpoint operating and migration guide](INAT_COMMITTED_CHECKPOINTS_BATCH8.md). Mutable upstream offset ordering, single-writer storage and replay after uncertain commits remain limitations. |

## Validation and reproduction

The release checkout passes **235 distinct selected cases**. Repeated adapter cases in earlier package runs are counted only once. These are bounded offline contract tests, not a full system, cloud, database or hardware qualification.

| Group | Cases | Evidence exercised |
|---|---:|---|
| Search adapter and metering, plus existing API/OpenAPI checks | 15 | Actual FastAPI models/routes and PostgreSQL parameter compilation with dependency overrides |
| Filter cache and explicit incomplete search | 23 | Real dispatcher/cache code with in-memory cache and a session protocol fixture |
| Snapshot selection and metadata, plus existing snapshot tests | 29 | Real temporary SQLite SELECTs through an adapter; recording writer and mocked governance |
| MDP integrity and framing | 72 | 30 existing and 42 new cases; independent CRC oracle and malformed frame boundaries |
| Bulk transaction accounting | 13 | Real SQLite/SQLAlchemy savepoints, commit failure and partial row failures through a dialect adapter |
| iNaturalist ingestion and committed checkpoints | 83 | 36 prior ingestion cases and 47 checkpoint cases; actual database-session helper with temporary SQLite and mocked upstream |

Dependencies are declared in `pyproject.toml` (`test` extras). The recorded environment uses Python 3.12, pytest 8.4.2, pytest-asyncio 0.23.8, SQLAlchemy 2.0.49 and FastAPI 0.111.1. Run in a disposable checkout without production environment files. For example, from the repository root in PowerShell, use separate processes for these fixture groups:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -B -m pytest --noconftest -p pytest_asyncio.plugin -p no:cacheprovider -o addopts= -q tests/test_worldview_search_contract.py tests/test_worldview_metering_contract.py tests/test_api_routes.py tests/test_api_contract_openapi.py tests/test_batch5_search_contract.py tests/test_batch6_snapshot_contract.py tests/test_worldview_snapshots.py
python -B -m pytest --noconftest -p pytest_asyncio.plugin -p no:cacheprovider -o addopts= -q tests/test_mdp_protocol.py tests/test_batch7_mdp_integrity_contract.py
python -B -m pytest --noconftest -p pytest_asyncio.plugin -p no:cacheprovider -o addopts= -q tests/test_batch6_bulk_transaction_contract.py
python -B -m pytest --noconftest -p pytest_asyncio.plugin -p no:cacheprovider -o addopts= -q tests/test_batch8_inat_checkpoint_contract.py tests/test_batch5_inat_ingestion_contract.py
```

The audit also ran these scopes through external scratch runners with selected outbound socket and credential-filename guards. Those guards are not an operating-system sandbox. AST-based bulk/wrapper fixtures and temporary SQLite cannot validate PostgreSQL geography, migrations, async-driver behavior or deployed authorization. Existing Pydantic deprecation and duplicate OpenAPI operation-ID warnings remain. No live ingestion, customer transaction, device command or VM restart was performed by the tests.

The new checkpoint cases reproduced 43 failures and four passes against their frozen effective baseline; all 47 then passed with the repair, alongside the unchanged 36 ingestion cases. Other packages retain their separately recorded red/green evidence. `git diff --check` passes for the repair scope. Four pre-existing generated UTF8 root test logs are excluded from the release.

## Deployment acceptance

1. Validate current PostgreSQL/PostGIS migrations and repaired SQL against a disposable database, including a role unable to create schema, row failures, disconnects and commit acknowledgement failure. Do not use customer data as a test fixture.
2. Stage authenticated Worldview and Earth consumer requests, preserving 400/422/503 and incomplete/unknown metadata through each proxy and UI. Confirm the actual producer and reader schemas agree; ingestion uses `obs.observation`, while parts of unified search reference `core.observation` and a separate species mirror.
3. Stop the affected ETL job before checkpoint migration. Preserve the database and cursor together. Archive legacy cursors and restart explicitly from page one; do not relabel a legacy cursor as committed. Select durable storage and a single writer before enabling resume.
4. Preserve operator changes and current container mounts when updating a VM. Record the deployed commit, image digest, backup location, health check and consumer smoke-test result. A GitHub main merge alone is not a deployment receipt.

Open gaps include entitlement/quota enforcement, canonical cross-store identities, effective domain/time/region filtering, read-triggered acquisition, provider completeness, age-based freshness, durable messaging and cross-product consumer behavior. This release must not be described as closing all audit findings or making every data product production-ready.

## Rollback

Retain the previous application image and source checkout with its local configuration. Revert the release commits or switch back to the prior verified image while preserving unrelated operator files and database contents. No database migration is introduced by these patches. Reverting reintroduces the documented defects.

Checkpoint rollback needs special handling: stop the job, retain its version 2 cursor for diagnosis and explicitly restart old code from page one. Old code does not validate the new checkpoint proof fields. Already committed pages remain in the database. No physical device firmware, broker routing or wire dialect was changed by this release.
