# MINDEX AI Model Guide

This document is written for AI copilots (Cursor, MYCA agents, NatureOS workers, Autogen bots, etc.) that need an authoritative mental model of MINDEX. It captures how the system is wired today, the invariants you must protect, and the playbooks to follow when extending the stack.

## 1. Source-of-truth recap

- **Database:** PostgreSQL 16 + PostGIS (`migrations/0001_init.sql`), with schemas: `core`, `bio`, `obs`, `telemetry`, `ip`, `ledger`, and read-optimized `app.*` views.
- **API:** `mindex_api/` FastAPI app, async SQLAlchemy sessions, raw SQL queries, Pydantic response models.
- **ETL:** `mindex_etl/` synchronous jobs using psycopg + HTTP clients to ingest canonical data.
- **Ledger connectors:** `mindex_api/ledger/` modules encapsulate Hypergraph, Bitcoin Ordinals, and Solana persistence.
- **Tests:** `tests/` folder keeps API routes, ETL iterators, canonicalizers, and SQL smoke flows honest.

## 2. Component map

```
[External feeds] ─┐
                  ▼
        mindex_etl.sources.*  -->  mindex_etl.jobs.*  -->  PostgreSQL/PostGIS
                                                        ▲            │
                                                        │            ▼
                                                   FastAPI routers  Ledger helpers
                                                        │
                                      NatureOS • MYCA • device firmware • analytics
```

## 3. Domain entities (what lives where)

| Domain | Tables / Views | Notes |
| ------ | -------------- | ----- |
| Taxonomy | `core.taxon`, `core.taxon_external_id`, `core.taxon_synonym`, `app.v_taxon_with_traits` | Canonical fungal species metadata. |
| Traits & Genomics | `bio.taxon_trait`, `bio.genome` | Derived from Mushroom.World, Wikipedia, FungiDB. |
| Observations | `obs.observation` | Crowd + device observations (GeoJSON). |
| Telemetry | `telemetry.device`, `telemetry.stream`, `telemetry.sample`, `app.v_device_latest_samples` | Devices, streams, last-sample rollup. |
| IP assets | `ip.ip_asset` | Describes datasets/devices/digital assets. |
| Ledger | `ledger.hypergraph_anchor`, `ledger.bitcoin_ordinal`, `ledger.solana_binding` | Provenance bindings. |

## 4. API contract reference

### Auth, headers, and pagination

- Health route is public; everything else requires `X-API-Key` matching `API_KEYS` (from `.env`).
- Pagination query params: `limit` (1–100), `offset` (>=0). Defaults come from `settings.default_page_size`.
- Geo filters (`/observations`) use `bbox=minLon,minLat,maxLon,maxLat` (WGS84) and ISO timestamps (`start`, `end`).
- Responses always wrap `data` + `pagination` (see `mindex_api/schemas/common.py`).

### Read endpoints

| Path | Method | Purpose | SQL source |
| ---- | ------ | ------- | ---------- |
| `/health` | GET | DB heartbeat | `SELECT 1` |
| `/taxa` | GET | Search canonical/common names, filter by rank | `core.taxon` |
| `/taxa/{id}` | GET | Single taxon + aggregated traits | `app.v_taxon_with_traits` |
| `/observations` | GET | Observation feed with bbox/time filters | `obs.observation` |
| `/devices` | GET | Telemetry devices + location GeoJSON | `telemetry.device` |
| `/telemetry/devices/latest` | GET | Flattened device + latest sample data | `app.v_device_latest_samples` |
| `/ip/assets` | GET | Asset catalog + ledger joins | `ip.ip_asset` with lateral joins |
| `/ip/assets/{id}` | GET | Single asset snapshot | same as list query |

### Write endpoints

| Path | Method | Payload model | Side effect |
| ---- | ------ | ------------- | ----------- |
| `/ip/assets/{id}/anchor/hypergraph` | POST | `HypergraphAnchorRequest` (Base64 payload, metadata, optional sample_id) | Hash payload via `hash_dataset` and insert into `ledger.hypergraph_anchor`. |
| `/ip/assets/{id}/anchor/ordinal` | POST | `OrdinalAnchorRequest` (payload, inscription metadata) | Hash payload, insert `ledger.bitcoin_ordinal`. |
| `/ip/assets/{id}/bind/solana` | POST | `SolanaBindingRequest` | Insert `ledger.solana_binding`. |

All write routes first verify the asset exists (`_ensure_asset_exists`). Real integration with Hypergraph / Ordinals / Solana RPC can be layered into the ledger helpers without touching routers.

## 5. Agent use cases

- **NatureOS (read-heavy dashboards):** lean on `/taxa`, `/observations`, `/telemetry/devices/latest`, `/ip/assets`. Enforce pagination and caching; prefer read-only API key.
- **MYCA agents (read + curate):** same reads plus ledger POST routes to anchor novel datasets. When writing, always attach metadata describing the producing agent/run.
- **Device / firmware teams:** typically only call `/devices` and `/telemetry/devices/latest`. When ingesting telemetry, write to DB via pipelines outside this repo; MINDEX remains canonical API.

## 6. ETL playbooks

### Shared helpers (`mindex_etl/taxon_canonicalizer.py`)

- `normalize_name(name)` strips whitespace and collapses multi-space sequences.
- `upsert_taxon(...)` ensures `(canonical_name, rank)` uniqueness, updates metadata, and returns the UUID.
- `link_external_id(...)` idempotently inserts rows into `core.taxon_external_id`.

### `sync_inat_taxa.py`

- **Source:** iNaturalist `/taxa` endpoint (`sources/inat.py` with `FUNGI_TAXON_ID=47170`).
- **Flow:** paginate 100–200 records/page, map fields via `map_inat_taxon`, upsert taxon, then `link_external_id`.
- **CLI:** `python -m mindex_etl.jobs.sync_inat_taxa --per-page 200 --max-pages 10`.
- **Invariants:** API polite rate-limit (`delay_seconds`), retries handled by `tenacity`. Re-running is safe.

### `sync_mycobank_taxa.py`

- **Source:** MycoBank species search (`sources/mycobank.py`).
- **Flow:** iterate alphabetical prefixes, map record, upsert taxon, link MycoBank number, insert synonyms into `core.taxon_synonym`.
- **CLI:** `python -m mindex_etl.jobs.sync_mycobank_taxa --prefixes a,b,c`.
- **Invariants:** Deduplicate synonyms, respect API throttling—consider batching prefixes when running often.

### `sync_fungidb_genomes.py`

- **Source:** FungiDB genomes feed (`sources/fungidb.py`).
- **Flow:** upsert species stub (rank=species, source=fungidb), insert/upsert `bio.genome` rows keyed by `(source, accession)`.
- **CLI:** `python -m mindex_etl.jobs.sync_fungidb_genomes --max-pages 5`.
- **Invariants:** `ON CONFLICT` ensures metadata updates; capture `release_date` as DATE.

### `backfill_traits.py`

- **Source:** Mushroom.World species feed + optional Wikipedia summaries.
- **Flow:** upsert taxon (includes description/common name), iterate trait pairs, insert into `bio.taxon_trait` if absent. Optional Wikipedia enrichment uses `extract_traits` for edibility/ecology fields.
- **CLI:** `python -m mindex_etl.jobs.backfill_traits --skip-wikipedia`.
- **Invariants:** Each `(taxon_id, trait_name, value_text)` combination is unique; avoid duplicates.

### Operational notes

- All jobs require `DATABASE_URL`. Keep secrets out of the repo; load via `.env`.
- Use smaller page counts during automated validation to avoid hammering upstream services.
- Wrap new sources by adding modules under `mindex_etl/sources/` and referencing them from future jobs.

## 7. Ledger workflows

1. **Hypergraph anchor**  
   - Hash bytes via `ledger.hypergraph.hash_dataset`.  
   - Insert row with optional `sample_id` link.  
   - Return hex digest plus metadata; currently a stub for future Hypergraph RPC integration.

2. **Bitcoin Ordinal**  
   - `record_bitcoin_ordinal` hashes payload, stores inscription metadata (ID/address) and returns hex digest.  
   - Extend this module when wiring to the actual Ordinals stack.

3. **Solana binding**  
   - `record_solana_binding` stores mint + token account.  
   - Add Solana RPC verification later by enhancing this helper.

Keep ledger-specific logic inside `mindex_api/ledger/` so routers remain thin.

## 8. Coding & testing standards

- Target **Python 3.11**. Async DB access uses SQLAlchemy 2.x with `AsyncSession`; ETL jobs use sync psycopg.
- Formatting: `black` (line length 100), linting: `ruff` (pyproject configured). Run both before proposing changes.
- Prefer **explicit SQL** (`sqlalchemy.text(...)`) to stay aligned with curated database views/migrations.
- **Tests to run before submitting changes:**
  - `pytest tests/test_api_routes.py`
  - `pytest tests/test_sources_inat.py`
  - `pytest tests/test_taxon_canonicalizer.py`
  - `pytest tests/test_sql_smoke.py` (requires running Postgres; auto-skips otherwise)

## 9. Common recipes for AI contributors

### Add a new API route

1. Create/extend a Pydantic schema under `mindex_api/schemas/`.
2. Add route logic under `mindex_api/routers/`, reusing `pagination_params` + `require_api_key`.
3. Issue SQL via `text()`; prefer views or create new read-only SQL functions rather than ORM models.
4. Add unit tests using dependency overrides in `tests/test_api_routes.py` style.
5. Document the route in `README.md` and (if applicable) extend this guide.

### Add a new ETL job

1. Build a source adapter under `mindex_etl/sources/` (HTTP client, retries, mapping).
2. If taxonomy-related, rely on `taxon_canonicalizer`. Otherwise, insert directly with psycopg cursors.
3. Create a CLI entry in `mindex_etl/jobs/your_job.py` with a `main()` wrapper.
4. Write unit tests using `respx`/`responses` to mock upstream APIs.
5. Update docs (`README.md`, this guide) to describe the new pipeline.

### Extend the database schema

1. Add a new SQL file under `migrations/` (incremental number). Include `CREATE EXTENSION` if needed.
2. Update any dependent views in the same migration.
3. Run `scripts/apply_migrations.py --dsn ...` locally, then execute `pytest tests/test_sql_smoke.py`.
4. Reflect schema in routers/schemas/tests as needed.

### Add a ledger integration

1. Extend or add modules under `mindex_api/ledger/`.
2. Keep routers simple by calling your helper functions.
3. Include metadata about the agent/process performing the anchor/binding.
4. Consider background tasks if the integration involves slow RPC calls.

## 10. Operational guardrails

- **Never commit secrets** (.env, API keys, certificates). `.env.example` is safe; `.env` is gitignored.
- **API keys**: For local dev, `docker-compose` sets `API_KEYS=local-dev-key`. Rotate keys per environment.
- **Pagination**: Always enforce `settings.max_page_size` to prevent runaway queries.
- **Geo columns**: Observations/telemetry store PostGIS geography; always use SRID 4326.
- **Ledger writes**: Validate that `ip_asset_id` exists before writing ledger rows (router already enforces this).
- **Error handling**: ETL jobs must catch upstream failures; rely on `tenacity` decorators or wrap with try/except.

## 11. Quick command reference

```bash
# Install dev deps
pip install -e .[dev]

# Run FastAPI locally
uvicorn mindex_api.main:app --reload

# Apply SQL migrations
python scripts/apply_migrations.py --dsn postgresql://mindex:mindex@localhost:5432/mindex

# Execute ETL jobs
python -m mindex_etl.jobs.sync_inat_taxa --per-page 100
python -m mindex_etl.jobs.backfill_traits --skip-wikipedia

# Run tests
pytest
```

## 12. Additional references

- `README.md` – human-friendly overview and operator instructions.
- `mindex_api/README.md` – architecture recap focused on the API.
- `mindex_api/TASKS.md` – backlog/checklist for Cursor/MYCA.
- Database ERD + schema definitions live in `migrations/0001_init.sql`.

Keep this file close when planning or executing automated refactors—the guardrails here are optimized to keep MINDEX production-safe while still giving AI agents enough context to act autonomously. 🧠🍄

This document is written for AI copilots (Cursor, MYCA agents, NatureOS workers, Autogen bots, etc.) that need an authoritative mental model of MINDEX. It captures how the system is wired today, the invariants you must protect, and the playbooks to follow when extending the stack.

## 1. Source-of-truth recap

- **Database:** PostgreSQL 16 + PostGIS (`migrations/0001_init.sql`), with schemas: `core`, `bio`, `obs`, `telemetry`, `ip`, `ledger`, and read-optimized `app.*` views.
- **API:** `mindex_api/` FastAPI app, async SQLAlchemy sessions, raw SQL queries, Pydantic response models.
- **ETL:** `mindex_etl/` synchronous jobs using psycopg + HTTP clients to ingest canonical data.
- **Ledger connectors:** `mindex_api/ledger/` modules encapsulate Hypergraph, Bitcoin Ordinals, and Solana persistence.
- **Tests:** `tests/` folder keeps API routes, ETL iterators, canonicalizers, and SQL smoke flows honest.

## 2. Component map

```
[External feeds] ─┐
                  ▼
        mindex_etl.sources.*  -->  mindex_etl.jobs.*  -->  PostgreSQL/PostGIS
                                                        ▲            │
                                                        │            ▼
                                                   FastAPI routers  Ledger helpers
                                                        │
                                      NatureOS • MYCA • device firmware • analytics
```

## 3. Domain entities (what lives where)

| Domain | Tables / Views | Notes |
| ------ | -------------- | ----- |
| Taxonomy | `core.taxon`, `core.taxon_external_id`, `core.taxon_synonym`, `app.v_taxon_with_traits` | Canonical fungal species metadata. |
| Traits & Genomics | `bio.taxon_trait`, `bio.genome` | Derived from Mushroom.World, Wikipedia, FungiDB. |
| Observations | `obs.observation` | Crowd + device observations (GeoJSON). |
| Telemetry | `telemetry.device`, `telemetry.stream`, `telemetry.sample`, `app.v_device_latest_samples` | Devices, streams, last-sample rollup. |
| IP assets | `ip.ip_asset` | Describes datasets/devices/digital assets. |
| Ledger | `ledger.hypergraph_anchor`, `ledger.bitcoin_ordinal`, `ledger.solana_binding` | Provenance bindings. |

## 4. API contract reference

### Auth, headers, and pagination

- Health route is public; everything else requires `X-API-Key` matching `API_KEYS` (from `.env`).
- Pagination query params: `limit` (1–100), `offset` (>=0). Defaults come from `settings.default_page_size`.
- Geo filters (`/observations`) use `bbox=minLon,minLat,maxLon,maxLat` (WGS84) and ISO timestamps (`start`, `end`).
- Responses always wrap `data` + `pagination` (see `mindex_api/schemas/common.py`).

### Read endpoints

| Path | Method | Purpose | SQL source |
| ---- | ------ | ------- | ---------- |
| `/health` | GET | DB heartbeat | `SELECT 1` |
| `/taxa` | GET | Search canonical/common names, filter by rank | `core.taxon` |
| `/taxa/{id}` | GET | Single taxon + aggregated traits | `app.v_taxon_with_traits` |
| `/observations` | GET | Observation feed with bbox/time filters | `obs.observation` |
| `/devices` | GET | Telemetry devices + location GeoJSON | `telemetry.device` |
| `/telemetry/devices/latest` | GET | Flattened device + latest sample data | `app.v_device_latest_samples` |
| `/ip/assets` | GET | Asset catalog + ledger joins | `ip.ip_asset` with lateral joins |
| `/ip/assets/{id}` | GET | Single asset snapshot | same as list query |

### Write endpoints

| Path | Method | Payload model | Side effect |
| ---- | ------ | ------------- | ----------- |
| `/ip/assets/{id}/anchor/hypergraph` | POST | `HypergraphAnchorRequest` (Base64 payload, metadata, optional sample_id) | Hash payload via `hash_dataset` and insert into `ledger.hypergraph_anchor`. |
| `/ip/assets/{id}/anchor/ordinal` | POST | `OrdinalAnchorRequest` (payload, inscription metadata) | Hash payload, insert `ledger.bitcoin_ordinal`. |
| `/ip/assets/{id}/bind/solana` | POST | `SolanaBindingRequest` | Insert `ledger.solana_binding`. |

All write routes first verify the asset exists (`_ensure_asset_exists`). Real integration with Hypergraph / Ordinals / Solana RPC can be layered into the ledger helpers without touching routers.

## 5. Agent use cases

- **NatureOS (read-heavy dashboards):** lean on `/taxa`, `/observations`, `/telemetry/devices/latest`, `/ip/assets`. Enforce pagination and caching; prefer read-only API key.
- **MYCA agents (read + curate):** same reads plus ledger POST routes to anchor novel datasets. When writing, always attach metadata describing the producing agent/run.
- **Device / firmware teams:** typically only call `/devices` and `/telemetry/devices/latest`. When ingesting telemetry, write to DB via pipelines outside this repo; MINDEX remains canonical API.

## 6. ETL playbooks

### Shared helpers (`mindex_etl/taxon_canonicalizer.py`)

- `normalize_name(name)` strips whitespace and collapses multi-space sequences.
- `upsert_taxon(...)` ensures `(canonical_name, rank)` uniqueness, updates metadata, and returns the UUID.
- `link_external_id(...)` idempotently inserts rows into `core.taxon_external_id`.

### `sync_inat_taxa.py`

- **Source:** iNaturalist `/taxa` endpoint (`sources/inat.py` with `FUNGI_TAXON_ID=47170`).
- **Flow:** paginate 100–200 records/page, map fields via `map_inat_taxon`, upsert taxon, then `link_external_id`.
- **CLI:** `python -m mindex_etl.jobs.sync_inat_taxa --per-page 200 --max-pages 10`.
- **Invariants:** API polite rate-limit (`delay_seconds`), retries handled by `tenacity`. Re-running is safe.

### `sync_mycobank_taxa.py`

- **Source:** MycoBank species search (`sources/mycobank.py`).
- **Flow:** iterate alphabetical prefixes, map record, upsert taxon, link MycoBank number, insert synonyms into `core.taxon_synonym`.
- **CLI:** `python -m mindex_etl.jobs.sync_mycobank_taxa --prefixes a,b,c`.
- **Invariants:** Deduplicate synonyms, respect API throttling—consider batching prefixes when running often.

### `sync_fungidb_genomes.py`

- **Source:** FungiDB genomes feed (`sources/fungidb.py`).
- **Flow:** upsert species stub (rank=species, source=fungidb), insert/upsert `bio.genome` rows keyed by `(source, accession)`.
- **CLI:** `python -m mindex_etl.jobs.sync_fungidb_genomes --max-pages 5`.
- **Invariants:** `ON CONFLICT` ensures metadata updates; capture `release_date` as DATE.

### `backfill_traits.py`

- **Source:** Mushroom.World species feed + optional Wikipedia summaries.
- **Flow:** upsert taxon (includes description/common name), iterate trait pairs, insert into `bio.taxon_trait` if absent. Optional Wikipedia enrichment uses `extract_traits` for edibility/ecology fields.
- **CLI:** `python -m mindex_etl.jobs.backfill_traits --skip-wikipedia`.
- **Invariants:** Each `(taxon_id, trait_name, value_text)` combination is unique; avoid duplicates.

### Operational notes

- All jobs require `DATABASE_URL`. Keep secrets out of the repo; load via `.env`.
- Use smaller page counts during automated validation to avoid hammering upstream services.
- Wrap new sources by adding modules under `mindex_etl/sources/` and referencing them from future jobs.

## 7. Ledger workflows

1. **Hypergraph anchor**  
   - Hash bytes via `ledger.hypergraph.hash_dataset`.  
   - Insert row with optional `sample_id` link.  
   - Return hex digest plus metadata; currently a stub for future Hypergraph RPC integration.

2. **Bitcoin Ordinal**  
   - `record_bitcoin_ordinal` hashes payload, stores inscription metadata (ID/address) and returns hex digest.  
   - Extend this module when wiring to the actual Ordinals stack.

3. **Solana binding**  
   - `record_solana_binding` stores mint + token account.  
   - Add Solana RPC verification later by enhancing this helper.

Keep ledger-specific logic inside `mindex_api/ledger/` so routers remain thin.

## 8. Coding & testing standards

- Target **Python 3.11**. Async DB access uses SQLAlchemy 2.x with `AsyncSession`; ETL jobs use sync psycopg.
- Formatting: `black` (line length 100), linting: `ruff` (pyproject configured). Run both before proposing changes.
- Prefer **explicit SQL** (`sqlalchemy.text(...)`) to stay aligned with curated database views/migrations.
- **Tests to run before submitting changes:**
  - `pytest tests/test_api_routes.py`
  - `pytest tests/test_sources_inat.py`
  - `pytest tests/test_taxon_canonicalizer.py`
  - `pytest tests/test_sql_smoke.py` (requires running Postgres; auto-skips otherwise)

## 9. Common recipes for AI contributors

### Add a new API route

1. Create/extend a Pydantic schema under `mindex_api/schemas/`.
2. Add route logic under `mindex_api/routers/`, reusing `pagination_params` + `require_api_key`.
3. Issue SQL via `text()`; prefer views or create new read-only SQL functions rather than ORM models.
4. Add unit tests using dependency overrides in `tests/test_api_routes.py` style.
5. Document the route in `README.md` and (if applicable) extend this guide.

### Add a new ETL job

1. Build a source adapter under `mindex_etl/sources/` (HTTP client, retries, mapping).
2. If taxonomy-related, rely on `taxon_canonicalizer`. Otherwise, insert directly with psycopg cursors.
3. Create a CLI entry in `mindex_etl/jobs/your_job.py` with a `main()` wrapper.
4. Write unit tests using `respx`/`responses` to mock upstream APIs.
5. Update docs (`README.md`, this guide) to describe the new pipeline.

### Extend the database schema

1. Add a new SQL file under `migrations/` (incremental number). Include `CREATE EXTENSION` if needed.
2. Update any dependent views in the same migration.
3. Run `scripts/apply_migrations.py --dsn ...` locally, then execute `pytest tests/test_sql_smoke.py`.
4. Reflect schema in routers/schemas/tests as needed.

### Add a ledger integration

1. Extend or add modules under `mindex_api/ledger/`.
2. Keep routers simple by calling your helper functions.
3. Include metadata about the agent/process performing the anchor/binding.
4. Consider background tasks if the integration involves slow RPC calls.

## 10. Operational guardrails

- **Never commit secrets** (.env, API keys, certificates). `.env.example` is safe; `.env` is gitignored.
- **API keys**: For local dev, `docker-compose` sets `API_KEYS=local-dev-key`. Rotate keys per environment.
- **Pagination**: Always enforce `settings.max_page_size` to prevent runaway queries.
- **Geo columns**: Observations/telemetry store PostGIS geography; always use SRID 4326.
- **Ledger writes**: Validate that `ip_asset_id` exists before writing ledger rows (router already enforces this).
- **Error handling**: ETL jobs must catch upstream failures; rely on `tenacity` decorators or wrap with try/except.

## 11. Quick command reference

```bash
# Install dev deps
pip install -e .[dev]

# Run FastAPI locally
uvicorn mindex_api.main:app --reload

# Apply SQL migrations
python scripts/apply_migrations.py --dsn postgresql://mindex:mindex@localhost:5432/mindex

# Execute ETL jobs
python -m mindex_etl.jobs.sync_inat_taxa --per-page 100
python -m mindex_etl.jobs.backfill_traits --skip-wikipedia

# Run tests
pytest
```

## 12. Additional references

- `README.md` – human-friendly overview and operator instructions.
- `mindex_api/README.md` – architecture recap focused on the API.
- `mindex_api/TASKS.md` – backlog/checklist for Cursor/MYCA.
- Database ERD + schema definitions live in `migrations/0001_init.sql`.

Keep this file close when planning or executing automated refactors—the guardrails here are optimized to keep MINDEX production-safe while still giving AI agents enough context to act autonomously. 🧠🍄
