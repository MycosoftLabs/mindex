# MINDEX – Mycological Intelligence Exchange

MINDEX is Mycosoft’s canonical fungal knowledge graph: a Postgres/PostGIS database, FastAPI service layer, ETL pipelines, and ledger adapters that tie live telemetry, taxonomy, and IP assets into NatureOS, MYCA agents, and device firmware.

## Why it matters

- Centralizes canonical fungal taxonomy (`core` schema) with traits, genomes, observations, and telemetry enrichments.
- Normalizes ingestion from public (iNaturalist, MycoBank, FungiDB, Mushroom.World, Wikipedia) and proprietary streams via resilient ETL jobs.
- Exposes a single FastAPI surface for NatureOS dashboards, MYCA multi-agent workflows, and internal device teams.
- Anchors high-value datasets and telemetry to Hypergraph, Bitcoin Ordinals, and Solana for IP provenance.
- Ships as a Docker/ACR image and can harden for Platform One / Iron Bank deployments.

## System overview

MINDEX is intentionally small but layered:

```
┌────────────┐    ingest    ┌────────────┐    async SQLA    ┌──────────────┐
│ External   │ ───────────▶ │  mindex_etl│ ───────────────▶ │ Postgres +   │
│ data/APIs  │              │  jobs      │                  │ PostGIS      │
└────────────┘              └────────────┘                  │ (core/bio/…) │
                                                               ▲      │
                                                               │      ▼
                                                        ┌──────────────┐
                                                        │ FastAPI app │
                                                        │ mindex_api  │
                                                        └──────────────┘
                                                               │REST
                                                               ▼
                                                        NatureOS • MYCA • Devices
```

- **Database schemas** (`migrations/0001_init.sql`): `core`, `bio`, `obs`, `telemetry`, `ip`, `ledger`, plus read-optimized `app.*` views.
- **API** (`mindex_api/`): Async SQLAlchemy + FastAPI routers with strict Pydantic contracts, API-key guard, and ledger helpers.
- **ETL** (`mindex_etl/`): Source-specific adapters, taxon canonicalization helpers, and runnable jobs.
- **Ledger** (`mindex_api/ledger/`): Hypergraph hashing, Bitcoin Ordinals inscriptions, Solana binding utilities.

## Repository layout

```
mindex/
├── README.md
├── pyproject.toml
├── docker-compose.yml / Dockerfile
├── migrations/
│   └── 0001_init.sql
├── scripts/
│   └── apply_migrations.py
├── mindex_api/
│   ├── main.py / config.py / db.py / dependencies.py
│   ├── routers/ (health, taxon, telemetry, devices, observations, ip_assets)
│   ├── schemas/ (Pydantic response models)
│   ├── ledger/ (hypergraph, bitcoin_ordinals, solana)
│   └── TASKS.md (long-form build checklist)
├── mindex_etl/
│   ├── config.py / db.py / taxon_canonicalizer.py
│   ├── sources/ (inat, mycobank, fungidb, mushroom_world, wikipedia)
│   └── jobs/ (sync_* and backfill_traits)
├── tests/
│   ├── test_api_routes.py
│   ├── test_sources_inat.py
│   ├── test_taxon_canonicalizer.py
│   └── test_sql_smoke.py
├── .github/workflows/
│   ├── build-and-deploy-azure.yml
│   └── platform-one-build.yml
└── docs/
    └── AI_MODELS.md   # playbook for AI/agent contributors
```

## API surface (FastAPI `mindex_api/`)

All routes except `/health` require the `X-API-Key` header whenever `API_KEYS` is configured. Pagination uses the shared `pagination_params` dependency (default 25/offset 0, capped at 100).

| Method | Path | Purpose | Module |
| ------ | ---- | ------- | ------ |
| GET | `/health` | Liveness + DB connectivity check. | `routers/health.py` |
| GET | `/taxa` | Search canonical taxa (query + rank filters). | `routers/taxon.py` |
| GET | `/taxa/{id}` | Single taxon enriched with `app.v_taxon_with_traits`. | `routers/taxon.py` |
| GET | `/devices` | Device inventory with optional status filter + GeoJSON. | `routers/telemetry.py` |
| GET | `/telemetry/devices/latest` | Latest telemetry samples per device/stream. | `routers/telemetry.py` |
| GET | `/observations` | Filterable observations (taxon, time, bbox). | `routers/observations.py` |
| GET | `/ip/assets` | IP asset catalog with joined ledger bindings. | `routers/ip_assets.py` |
| GET | `/ip/assets/{id}` | Single asset with ledger/anchor history. | `routers/ip_assets.py` |
| POST | `/ip/assets/{id}/anchor/hypergraph` | Hash payload + persist Hypergraph anchor row. | `routers/ip_assets.py` |
| POST | `/ip/assets/{id}/anchor/ordinal` | Register Ordinal inscription metadata. | `routers/ip_assets.py` |
| POST | `/ip/assets/{id}/bind/solana` | Link asset to Solana mint/token accounts. | `routers/ip_assets.py` |

Schemas in `mindex_api/schemas/*.py` define exact JSON contracts, ensuring parity between docs and runtime responses. Database access is raw SQL via SQLAlchemy’s `text()` helpers to stay close to curated SQL views.

## ETL + data ingestion (`mindex_etl/`)

- `taxon_canonicalizer.py` normalizes species names, upserts rows into `core.taxon`, and links external IDs.
- **Source adapters** under `sources/` wrap the upstream APIs with retries (`tenacity`) and deterministic mapping helpers.
- **Jobs** under `jobs/` orchestrate inserts:
  - `sync_inat_taxa.py` – species crawl from iNaturalist, populates taxonomy + external IDs.
  - `sync_mycobank_taxa.py` – pulls authoritative names + synonyms.
  - `sync_fungidb_genomes.py` – stores genome metadata under `bio.genome`.
  - `backfill_traits.py` – ingests Mushroom.World traits and enriches with Wikipedia summaries.
- `scripts/apply_migrations.py` runs SQL migrations via psycopg or psql; invoke before ETL to guarantee schema parity.

Each job is idempotent by key (`ON CONFLICT` or existence checks) so re-running is safe. Run jobs directly (`python -m mindex_etl.jobs.sync_inat_taxa --per-page 200`) or from orchestration tooling.

## Ledger connectors

- `ledger/hypergraph.py` hashes payloads (`SHA-256`) and inserts into `ledger.hypergraph_anchor`, optionally linking telemetry samples.
- `ledger/bitcoin_ordinals.py` stores content hashes + Ordinal metadata (`ledger.bitcoin_ordinal`).
- `ledger/solana.py` binds assets to Solana mint/token rows (`ledger.solana_binding`).

Routers rely on these helpers so any persistence logic changes stay centralized.

## Local development

1. **Copy env** – `cp .env.example .env` (fill DB + API key secrets).
2. **Install tools** – Python 3.11, Docker (optional), `psycopg>=3`, PostGIS-enabled PostgreSQL.
3. **Set up virtualenv** (recommended): `python -m venv .venv && source .venv/bin/activate`.
4. **Install dependencies** – `pip install -e .[dev]`.
5. **Start Postgres/PostGIS** – either `docker-compose up db -d` or point to an existing cluster.
6. **Apply migrations** – `python scripts/apply_migrations.py --dsn postgresql://...`.
7. **Run the API** – `uvicorn mindex_api.main:app --reload --host 0.0.0.0 --port 8000`.
8. **Hit docs** – http://localhost:8000/docs (remember `X-API-Key` header if enabled).

### Docker Compose workflow

```bash
docker-compose up --build
# API: http://localhost:8000 | DB: localhost:5432
```

### Running ETL jobs

```bash
python -m mindex_etl.jobs.sync_inat_taxa --per-page 200
python -m mindex_etl.jobs.sync_mycobank_taxa --prefixes a,b,c
python -m mindex_etl.jobs.sync_fungidb_genomes --max-pages 5
python -m mindex_etl.jobs.backfill_traits --skip-wikipedia
```

All jobs rely on `DATABASE_URL` from `.env`. Use smaller page sizes in CI to keep runs fast.

## Configuration reference

### API (`mindex_api/config.py`)

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `MINDEX_DB_HOST/PORT/USER/PASSWORD/NAME` | `localhost/5432/mindex` | Postgres connection pieces combined into the async DSN. |
| `API_TITLE`, `API_VERSION` | `MINDEX API` / `0.1.0` | Metadata for OpenAPI. |
| `API_HOST`, `API_PORT` | `0.0.0.0`, `8000` | Uvicorn bind options. |
| `API_CORS_ORIGINS` | empty list | Optional CORS allowlist. |
| `API_KEYS` | empty list | Enables `X-API-Key` guard when populated. |
| `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE` | `25`, `100` | Pagination guardrails. |
| `TELEMETRY_LATEST_LIMIT` | `100` | Soft limit to view size. |
| `HYPERGRAPH_ENDPOINT`, `BITCOIN_ORDINAL_ENDPOINT`, `SOLANA_RPC_URL` | `None` | Optional downstream integration targets. |

### ETL (`mindex_etl/config.py`)

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DATABASE_URL` | `postgresql://mindex:mindex@localhost:5432/mindex` | Sync psycopg DSN. |
| `HTTP_TIMEOUT` / `HTTP_RETRIES` | `30s` / `3` | Shared HTTP client guards. |
| `INAT_BASE_URL`, `MYCOBANK_BASE_URL`, `FUNGIDB_BASE_URL`, `MUSHROOM_WORLD_BASE_URL`, `WIKIPEDIA_API_URL` | see config | Remote API endpoints. |
| `HYPERGRAPH_WEBHOOK` | `None` | Optional callback for ledger events. |

## Testing & quality

- `pytest` runs the whole suite; DB tests auto-skip without Postgres (`tests/test_sql_smoke.py`).
- API tests (`tests/test_api_routes.py`) override dependencies to stay hermetic.
- ETL/unit tests (`tests/test_sources_inat.py`, `tests/test_taxon_canonicalizer.py`) exercise iterators and helpers.
- Ruff/Black configs live in `pyproject.toml` (Python 3.11, line length 100). Run `ruff check .` and `black .` before committing.

## Deployments

- **Dockerfile** builds from `python:3.11-slim`, installs the project, exposes port 8000, and runs uvicorn.
- **Azure Container Apps**: `.github/workflows/build-and-deploy-azure.yml` runs tests, builds/pushes to ACR, and updates the target Container App using `az containerapp update`.
- **Platform One / Iron Bank**: `.github/workflows/platform-one-build.yml` reuses the test stage, builds against a hardened base image, and pushes to the Iron Bank registry when creds are present.

Tag images manually (`docker build -t mycosoft/mindex-api:dev .`) for local smoke, or rely on the GitHub Action SHA tags in CI/CD.

## Troubleshooting & ops hints

- Run `scripts/apply_migrations.py --dry-run` to confirm upcoming SQL files.
- Telemetry endpoints expect PostGIS geography columns; ensure `CREATE EXTENSION postgis;` ran (included in `0001_init.sql`).
- If `/observations` bbox queries fail, double-check SRID (GeoJSON should be WGS84).
- Ledger POST routes simply persist metadata today; hook actual Hypergraph/Ordinal APIs by extending `mindex_api/ledger/*.py`.
- Shared dev API key appears in `docker-compose.yml` (`local-dev-key`)—rotate for staging/prod.

## Additional docs

- `mindex_api/README.md` – API/DB deep dive.
- `mindex_api/TASKS.md` – authoritative backlog/checklist for MYCA/Cursor.
- `docs/AI_MODELS.md` – guidance for AI agents (NatureOS, MYCA, Cursor) contributing to MINDEX.
- docs/MYCOBRAIN_INTEGRATION.md - MycoBrain V1 integration guide (schema + endpoints + flows).
- MYCOBRAIN_INTEGRATION_SUMMARY.md - Change summary of the MycoBrain integration rollout.

Questions? Open an issue or tag the MINDEX platform team. Happy hacking! 🧫🍄
