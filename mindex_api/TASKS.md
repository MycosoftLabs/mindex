
---

## 3) `TASKS.md` checklist for Cursor

Create `TASKS.md` and paste:

```markdown
# MINDEX Build Tasks

This file is the build checklist for Cursor / MYCA to complete MINDEX.

---

## 1. Database layer

- [x] Confirm `migrations/0001_init.sql` runs cleanly on Postgres 16 with PostGIS.
  - [x] Ensure `CREATE EXTENSION postgis;` is included.
  - [x] Ensure `uuid-ossp` and `pgcrypto` extensions are created.
- [x] Add a `Makefile` or simple script to apply `migrations/*.sql` to a target DB.
- [x] Add basic SQL smoke tests:
  - [x] Insert a `core.taxon` row.
  - [x] Insert a `telemetry.device`, `telemetry.stream`, and `telemetry.sample` row.
  - [x] Query `app.v_taxon_with_traits` and `app.v_device_latest_samples`.

---

## 2. FastAPI API (`mindex_api/`)

- [x] Finalize `pyproject.toml` (dependencies/versions).
- [x] Validate `mindex_api/config.py` and `db.py` work with async SQLAlchemy + asyncpg.
- [x] Confirm routers:
  - [x] `/health` – operational health check.
  - [x] `/taxa` – list taxa with query, pagination.
  - [x] `/taxa/{id}` – fetch specific taxon.
  - [x] `/telemetry/devices/latest` – view from `app.v_device_latest_samples`.

- [x] Add more endpoints (read-only initially):
  - [x] `/observations` – read from `obs.observation` with filters (taxon, bbox, time).
  - [x] `/devices` – list telemetry devices.
  - [x] `/ip/assets` – list IP assets and links to ledger bindings.

- [x] Add error handling and Pydantic responses for the new endpoints.
- [x] Add unit tests for routers (pytest + httpx TestClient).

---

## 3. ETL (`mindex_etl/`)

### Shared utilities

- [x] Ensure `mindex_etl/db.py` exposes a sync DB connection for ETL jobs.
- [x] Implement `mindex_etl/taxon_canonicalizer.py`:
  - [x] `normalize_name`
  - [x] `upsert_taxon`
  - [x] `link_external_id`

### Source: iNaturalist

- [x] Implement `sources/inat.py`:
  - [x] Pagination over Fungi taxa.
  - [x] Rate-limit and retry logic.
  - [x] Map fields into `core.taxon` and `core.taxon_external_id`.
- [x] Implement `jobs/sync_inat_taxa.py` with a simple CLI `main()`.

### Source: MycoBank

- [x] Implement `sources/mycobank.py`:
  - [x] Scrape or use API to fetch fungal taxa and synonyms.
  - [x] Map MycoBank numbers and names to `core.taxon` + synonyms.
- [x] Implement `jobs/sync_mycobank_taxa.py`.

### Source: FungiDB

- [x] Implement `sources/fungidb.py`:
  - [x] Fetch genome metadata for fungal taxa.
  - [x] Map into `bio.genome`.
- [x] Implement `jobs/sync_fungidb_genomes.py`.

### Source: Mushroom.World

- [x] Implement `sources/mushroom_world.py`:
  - [x] Fetch species records, traits, and text descriptions.
  - [x] Map into `core.taxon` and `bio.taxon_trait`.

### Source: Wikipedia

- [x] Implement `sources/wikipedia.py`:
  - [x] Fetch page summaries + infoboxes for fungal species names.
  - [x] Extract traits (edibility, ecology, etc.) where possible.
- [x] Implement `jobs/backfill_traits.py` to fill `bio.taxon_trait`.

---

## 4. Ledger integrations

Create a submodule: `mindex_api/ledger/`.

- [x] `mindex_api/ledger/hypergraph.py`
  - [x] `hash_dataset(payload: bytes) -> bytes`
  - [x] `anchor_to_hypergraph(mindex_hash: bytes, metadata: dict) -> HypergraphAnchorRecord`
  - [x] Functions to update `ledger.hypergraph_anchor` and link to `telemetry.sample` or `ip.ip_asset`.

- [x] `mindex_api/ledger/bitcoin_ordinals.py`
  - [x] Placeholder functions to:
    - [x] Submit a file/hash for inscription (through existing Ordinals stack).
    - [x] Insert `ledger.bitcoin_ordinal` rows with `content_hash`, `inscription_id`, `inscription_address`.

- [x] `mindex_api/ledger/solana.py`
  - [x] Placeholder functions to:
    - [x] Bind an `ip_asset` to a Solana mint address + token account.
    - [x] Insert `ledger.solana_binding` rows.

- [x] Add FastAPI endpoints:
  - [x] POST `/ip/assets/{id}/anchor/hypergraph`
  - [x] POST `/ip/assets/{id}/anchor/ordinal`
  - [x] POST `/ip/assets/{id}/bind/solana`

These can be internal-only endpoints (secured later).

---

## 5. Security & auth

- [x] Add basic API key or JWT auth for non-health routes:
  - [x] A simple `X-API-Key` mechanism is acceptable for now.
  - [x] Wrap routers with dependency injection for auth.
- [ ] Prepare for mTLS / OIDC for Platform One deployment (configuration stubs only).

---

## 6. CI / CD

### Azure

- [x] Verify `.github/workflows/build-and-deploy-azure.yml`:
  - [x] Build on `main` pushes.
  - [x] Push image to Azure Container Registry.
  - [x] Update target Container App / Web App.

### Platform One

- [x] Verify `.github/workflows/platform-one-build.yml`:
  - [x] Build hardened image using Iron Bank base image.
  - [x] Push to DoD registry when credentials are provided.

---

## 7. Integration with NatureOS & MYCA

- [ ] Define a simple internal client library (`mindex_client.py`) for other services:
  - [ ] `get_taxon(id)`
  - [ ] `search_taxa(q)`
  - [ ] `get_latest_telemetry(device_id)`
- [ ] Document how NatureOS backend calls MINDEX API.
- [ ] Document how MYCA agents should write:
  - [ ] New traits
  - [ ] New IP assets
  - [ ] New telemetry streams/samples

---

## 8. Testing & validation

- [x] Add unit tests for:
  - [x] Taxon canonicalization
  - [x] iNat ETL job
  - [x] Basic API routes
- [x] Add a `pytest` job in CI.
- [ ] Add a `pre-commit` config for black/isort/mypy (optional).
