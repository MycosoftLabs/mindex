# MINDEX ETL Remediation Complete — Phases A–C (VM 189)

**Date:** June 10, 2026  
**Status:** Complete  
**VM:** 192.168.0.189  
**Related plan:** `docs/MINDEX_ETL_FULL_AUDIT_JUN10_2026.md`  
**Codex handoff:** `docs/CODEX_HANDOFF_EARTH_SIMULATOR_WEBSITE_APPS_JUN10_2026.md`

---

## Outcome summary

All three audit phases (A unblock, B domain smokes, C kingdom backfill + scheduler) are **verified on VM 189**. Non-taxonomy domains that were empty are now **populated or pipeline-ready**.

| Domain | Before | After (Jun 10 follow-ups) | Smoke |
|--------|--------|---------------------------|-------|
| Taxa `kingdom='Fungi'` | 3 | **521,931** | Kingdom backfill + canonicalizer |
| Compounds | 0 | **93** | PubChem + ChemSpider batch fallback |
| Genetic sequences | 0 (table missing) | **1,000** | GenBank 10-page bounded backfill |
| Genomes | partial | **331** | Existing + GenBank |
| Publications | 0 | **123** (120 PubMed + 3 GBIF) | PubMed + GBIF literature smoke |
| HQ media | bootstrap stub | **3 images** ingested | NAS mount + bounded live ingest |
| `mindex-etl` | unhealthy | **healthy** | Scheduler import healthcheck |
| `mindex-earth-sync` | unhealthy | **healthy** | Job-import healthcheck (not API :8000) |
| `mindex-api` | healthy | **healthy** | `/api/mindex/health` |

---

## Delivered (repo + VM)

### Code

- `mindex_etl/taxon_canonicalizer.py` — `normalize_kingdom()`, Fungi default for mycobank sources
- `mindex_etl/sources/mycobank.py` — explicit `kingdom: Fungi`
- `mindex_etl/sources/genbank.py` — retry without revoked NCBI API key on HTTP 400
- `mindex_etl/jobs/sync_genbank_genomes.py` — column alignment with `0012_genetics.sql`
- `mindex_etl/jobs/publications.py` — GBIF `/literature/search`, 429 retries, websites list parsing; default `pubmed`+`gbif`
- `mindex_etl/jobs/hq_media_ingestion.py` — async DB URL from docker env; taxon query fix
- `mindex_etl/sources/chemspider.py` — batch 400 fallback to per-record fetch
- `docker-compose.yml` — ETL `env_file`, ChemSpider/NCBI env, scheduler + earth-sync healthchecks
- `pyproject.toml` — `imagehash`, `Pillow`

### Migrations (VM 189 applied)

- `20260610_etl_schema_upgrade_JUN10_2026.sql`
- `20260610_pg_trgm_extension_JUN10_2026.sql`
- `0007_compounds.sql`, `0012_genetics.sql`
- `20260610_publications_schema_JUN10_2026.sql`
- `20260610_bio_sequence_grants_JUN10_2026.sql`
- `20260610_media_image_upgrade_JUN10_2026.sql`
- `20260610_media_image_vm189_JUN10_2026.sql` (no pgvector — VM postgis image lacks extension)
- `20260610_hq_media_columns_vm189_JUN10_2026.sql`
- `20260610_pgvector_optional_JUN10_2026.sql` (no-op on VM — eagle tables depend on broken vector lib; `embedding_json` canonical)

### Ops scripts

- `scripts/apply_etl_remediation_phases_abc_vm189.py`
- `scripts/finish_mindex_remediation_vm189.py`
- `scripts/run_remaining_smokes_vm189.py`
- `scripts/apply_media_migrations_vm189.py`
- `scripts/run_mindex_followups_vm189.py`
- `scripts/verify_mindex_followups_vm189.py`

### VM hygiene

- Revoked `NCBI_API_KEY` commented in `/home/mycosoft/mindex/.env` (rotate via env only)
- Kingdom backfill: **501,988** MycoBank rows → `Fungi`
- `bio` schema grants for role `mindex` (64 table grants + sequences)

---

## Verification commands

```bash
# On dev PC (loads VM_PASSWORD from MAS .credentials.local)
python scripts/run_remaining_smokes_vm189.py

# Counts on VM
ssh mycosoft@192.168.0.189
sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "
  SELECT 'compounds', count(*) FROM bio.compound
  UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence
  UNION ALL SELECT 'publications', count(*) FROM core.publications
  UNION ALL SELECT 'taxa_fungi', count(*) FROM core.taxon WHERE kingdom='Fungi';"

curl -s http://192.168.0.189:8000/api/mindex/health
```

---

## Follow-ups completed (Jun 10 evening)

| Item | Result |
|------|--------|
| **ChemSpider batch** | Per-record fallback on batch 400; smoke exit 0 |
| **GBIF literature** | Fixed URL + `websites` list parsing; 3 rows inserted in smoke |
| **Semantic Scholar** | 429 retry/backoff added; optional — not default source |
| **pgvector on VM** | Optional migration no-ops; `media.image` uses `embedding_json` |
| **HQ media ingest** | NAS mounted; **3** production images ingested |
| **mindex-earth-sync** | **healthy** — healthcheck imports `sync_earth_data` job |
| **GenBank backfill** | Bounded 10 pages → **1,000** sequences (800 insert, 200 update) |

### Remaining scale work (scheduler / ops, not blocking)

- Full GenBank corpus (~11M records) — run via scheduler over time
- ChemSpider compound IDs — batch API returns 400; compounds from PubChem path; chemspider_id column still 0
- Semantic Scholar — enable in `sources` when rate limits allow
- pgvector — upgrade postgis image or CASCADE eagle deps if native vectors needed on `media.image`

---

## Producer / website work

**Deferred per Morgan:** Producer program side panel and other website changes wait until this MINDEX remediation was complete. Codex may proceed with `docs/CODEX_HANDOFF_EARTH_SIMULATOR_WEBSITE_APPS_JUN10_2026.md` using live counts above.

---

## Lessons learned

1. Apply **sequence grants** after late-arriving migrations (`0012`) — `GRANT ON ALL SEQUENCES` is not retroactive for new sequences.
2. VM **bootstrap stubs** (`bio.compound`, `media.image`) must be detected and upgraded before ETL smokes.
3. ETL container **healthcheck must match process** (scheduler, not API port 8000).
4. Pin **numpy 1.26.x** after `imagehash` install on older CPUs without X86_V2.
