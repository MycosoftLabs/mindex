# MINDEX All-Life ETL — May 02, 2026

**Date:** 2026-05-02

## Summary

All-life ancestry expands taxon coverage beyond fungi: `core.taxon` gains kingdom + lineage, `bio.taxon_full` and `/all-life/*` API routes back rich profiles, and ETL defaults (`inat_domain_mode`, `gbif_domain_mode`) use **`all`**.

**List APIs** — optional query param `kingdom` on `GET /api/mindex/genetics`, `/genomes`, `/observations`, and `/compounds` (compounds filtered via `bio.taxon_compound` + taxon). `genomes` reads `bio.genome` (no hardcoded assembly payloads).

## Orchestration

- **Command:** `python -m mindex_etl.jobs.run_all_kingdoms` from repo root with `MINDEX_DATABASE_URL` or `DATABASE_URL` set for backfill when needed.  
- **Stubs:** `mindex_etl/sources/all_life_stubs.py` and per-source modules (`col`, `worms`, `obis`, …) return `status: not_implemented` **without writing rows**.

## n8n

- Import `mycosoft-mas/n8n/workflows/mindex_all_life_ingest.json` on the MAS n8n instance; replace the Code node with a secure runner (SSH/Execute) when the ETL host is fixed.

## Storage

- Prefer **taxonomy and media** pipelines before **large genomics** bulk loads; monitor `192.168.0.189` Postgres and Qdrant volume.
