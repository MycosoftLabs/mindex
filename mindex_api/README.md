# MINDEX – Mycological Index for Mycosoft

MINDEX is the canonical mycological database and API for Mycosoft’s ecosystem:

- **NatureOS** (environmental intelligence dashboard)
- **MYCA** multi-agent system
- **Devices** like Mushroom1, MycoNODE, SporeBase, Petraeus, ALARM
- **MycoDAO / MYCO token** (Solana) and IP anchoring on Bitcoin Ordinals / Hypergraph

The goal is to maintain the world’s most complete, normalized fungal knowledgebase and connect it to live telemetry and IP/ledger systems.

## Architecture

- **Database:** PostgreSQL 16 + PostGIS
- **Schemas:**
  - `core` – canonical taxonomy, external IDs, synonyms
  - `bio` – traits, genomes, compounds
  - `obs` – observations (iNaturalist, devices, lab)
  - `telemetry` – devices, streams, samples
  - `ip` – fungal IP assets, datasets, devices, strains
  - `ledger` – Hypergraph anchors, Bitcoin Ordinals, Solana bindings
  - `app` – app-specific views for NatureOS and MYCA

- **API:** `mindex_api/` (FastAPI) – read/write access for services and internal agents
- **ETL:** `mindex_etl/` (Python) – pulls data from:
  - iNaturalist
  - MycoBank
  - FungiDB
  - Mushroom.World
  - Wikipedia

- **Deployments:**
  - Local: Docker Compose
  - Azure: Azure Container Apps / Web App with ACR
  - DoD: Platform One / Iron Bank–style hardened image

## Repo layout

```text
mindex/
  README.md
  pyproject.toml
  .env.example
  docker-compose.yml
  Dockerfile

  migrations/
    0001_init.sql   # raw SQL to create MINDEX schemas & tables

  mindex_api/
    main.py
    config.py
    db.py
    dependencies.py
    routers/
      health.py
      taxon.py
      telemetry.py
    schemas/
      health.py
      taxon.py
      telemetry.py

  mindex_etl/
    config.py
    db.py
    taxon_canonicalizer.py
    sources/
      inat.py
      mycobank.py
      fungidb.py
      mushroom_world.py
      wikipedia.py
    jobs/
      sync_inat_taxa.py
      sync_mycobank_taxa.py
      sync_fungidb_genomes.py
      backfill_traits.py

  .github/
    workflows/
      build-and-deploy-azure.yml
      platform-one-build.yml
