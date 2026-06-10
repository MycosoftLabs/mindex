# MINDEX ETL Full Audit — Chemistry, Genetics, Images, Publications

**Date:** June 10, 2026  
**Status:** Audit complete — remediation **complete** (`MINDEX_ETL_REMEDIATION_COMPLETE_JUN10_2026.md`)  
**VM:** 192.168.0.189 (MINDEX)  
**Related:** `MINDEX_TAXA_REMEDIATION_COMPLETE_JUN10_2026.md`, `MINDEX_MISSING_TAXA_DIAGNOSIS_JUN10_2026.md`

---

## Executive summary

Taxonomy remediation **succeeded** (MycoBank ~100K taxa ingested). **Every non-taxonomy ETL domain is effectively empty** on VM 189: **0 compounds, 0 genomes, 0 publications, 0 HQ media images**.

Root causes cluster into three buckets:

1. **Database permissions** — ETL connects as role `mindex`; **`mindex` has zero grants on `bio.*`** (migration `20260603_grants_bio_obs_core.sql` never applied on VM).
2. **Schema drift** — VM has **bootstrap stubs** from `20260502_all_life_universal.sql` instead of full migrations (`0007_compounds.sql`, `0012_genetics.sql`). `bio.compound` lacks `pubchem_id` / `chemspider_id`; **`bio.genetic_sequence` does not exist**.
3. **Scheduler stall / crash** — `mindex-etl` is **unhealthy** (NumPy x86-v2 on old CPU). Jobs fail early (`fungidb`, `traits`, `inat_obs`) so **pubchem, chemspider, genetics, publications, hq_media never reach execution** in recent cycles.

---

## Live VM 189 snapshot (Jun 10, 2026 ~08:00 UTC)

| Domain | Table / signal | Count | Expected (docs/code) |
|--------|----------------|------:|----------------------|
| **Taxa** | `core.taxon` by source | mycobank **100,439**, inat **19,646**, gbif **3,372** | ✅ MycoBank bulk landed |
| **Kingdom** | `kingdom = 'Fungi'` | **3** | ❌ ~123K+ should be Fungi after backfill |
| **Kingdom** | `Undesignated` | **23,013** | ❌ Backfill not keeping pace |
| **Images (metadata)** | `metadata ? 'default_photo'` | **19,636** | ⚠️ URL refs only (iNat); not HQ pipeline |
| **Images (HQ)** | `media.image` | **0** | ❌ HQ ingestion never ran |
| **Observations** | `obs.observation` | **824,072** | ⚠️ Live sync blocked by kingdom constraint |
| **Compounds** | `bio.compound` | **0** | ❌ PubChem + ChemSpider |
| **Genetics** | `bio.genome` | **0** | ❌ FungiDB |
| **Genetics** | `bio.genetic_sequence` | **missing table** | ❌ GenBank job target |
| **Publications** | `core.publications` | **0** | ❌ PubMed / GBIF lit / Semantic Scholar |
| **Links** | `bio.taxon_compound`, `bio.publication_taxon` | **0** | ❌ Depends on compounds + pubs |

**ETL container:** `mindex-etl` — Up, **unhealthy**  
**ETL DB user:** `postgresql://mindex:***@db:5432/mindex` (no `bio` grants)

---

## ETL job registry vs reality

Jobs registered in `mindex_etl/jobs/run_all.py` and scheduled in `mindex_etl/scheduler.py`:

| Job | Source(s) | Schedule | Last run outcome | Data landed |
|-----|-----------|----------|------------------|-------------|
| `inat_taxa` | iNaturalist | 24h | ✅ ~10K records/cycle | ✅ |
| `mycobank` | MycoBank | 24h | ✅ 0 (already loaded) | ✅ 100K+ |
| `gbif` / `gbif_complete` | GBIF | 24h / 72h | Partial | ✅ rising |
| `kingdom_backfill` | MINDEX | 72h | Unknown / stalled | ❌ 3 Fungi |
| `theyeasts` | TheYeasts.org | 24h | 0 processed | ❌ scraper empty |
| `fusarium` | Fusarium.org | 24h | 0 species extracted | ❌ HTML parse broken |
| `mushroom_world` | Mushroom.World | 24h | 7 errors, 0 processed | ❌ |
| `fungidb` | FungiDB / VEuPathDB | 24h | **permission denied for schema bio** | ❌ |
| `traits` | Mushroom.World + Wikipedia | 24h | **permission denied for schema bio** | ❌ |
| `inat_obs` | iNaturalist | ~5 min | **taxon_kingdom_check** violation | ❌ blocked |
| `hq_media` | iNat/GBIF/Wikipedia | 12h | **Never reached** in recent logs | ❌ |
| `publications` | PubMed/GBIF/Semantic Scholar | 48h | **Never reached** | ❌ |
| `chemspider` | ChemSpider | 24h | **Never reached** | ❌ |
| `pubchem` | PubChem | 24h | **Never reached** | ❌ |
| `genetics` | GenBank | 24h | **Never reached** | ❌ |
| `taxon_photos` | iNaturalist | 24h | **Never reached** | ❌ |
| `ancestry` | MINDEX enrich | 1h | **Never reached** | ❌ |
| `civic_viewport` | Civic | 48h | Optional; NumPy import issues | N/A |

---

## Issues by domain

### P0 — Blocks all bio-plane ingestion

#### 1. `mindex` role lacks `bio` schema grants

**Evidence:** `information_schema.role_table_grants` returns **0 rows** for `grantee = 'mindex'` on `bio.*`. ETL logs:

```
Job fungidb failed: permission denied for schema bio
Job traits failed: permission denied for schema bio
```

**Fix:** On VM 189 Postgres, apply:

- `migrations/20260603_grants_bio_obs_core.sql`
- `migrations/20260603_ledger_grants.sql` (includes `media` schema for HQ pipeline)

Run as superuser (`mycosoft` or `postgres`) inside `mindex-postgres`.

**Verify:**

```sql
SELECT grantee, table_name FROM information_schema.role_table_grants
WHERE table_schema = 'bio' AND grantee = 'mindex' LIMIT 5;
```

---

#### 2. Missing / stub schema — compounds

**Evidence:** `bio.compound` on VM has only `id`, `name`, `source` (bootstrap from `20260502_all_life_universal.sql`). ETL `sync_pubchem_compounds.py` expects `pubchem_id`, `inchikey`, `formula`, etc. (`0007_compounds.sql`).

**Fix:** Apply `migrations/0007_compounds.sql` on VM (or run idempotent migration runner). Re-grant after new tables.

**Verify:** `\d bio.compound` shows `pubchem_id`, `chemspider_id`, `inchikey`.

---

#### 3. Missing table — genetics (GenBank)

**Evidence:** `bio.genetic_sequence` **does not exist**. `sync_genbank_genomes.py` inserts into `bio.genetic_sequence`. VM only has `bio.genome` (FungiDB-shaped, requires `taxon_id` FK).

**Fix:** Apply `migrations/0012_genetics.sql`. Optionally align FungiDB job to continue using `bio.genome` and GenBank to use `bio.genetic_sequence` (current code intent).

**Verify:**

```sql
SELECT count(*) FROM bio.genetic_sequence;
```

---

#### 4. ETL container unhealthy (NumPy)

**Evidence:** Scheduler crash: `NumPy was built with baseline optimizations` (x86-64-v2 CPU mismatch on VM 189).

**Fix:** Pin `numpy>=1.26.4,<2.0` in image (already in repo `pyproject.toml`); **rebuild** `mindex-etl` on VM. Until rebuild, runtime `pip install numpy==1.26.4` in container (temporary).

**Verify:** `docker ps` shows `mindex-etl` healthy; scheduler completes full job cycle without traceback.

---

### P1 — Data quality / observation pipeline

#### 5. `taxon_kingdom_check` blocks `inat_obs`

**Evidence:**

```
Job inat_obs failed: new row for relation "taxon" violates check constraint "taxon_kingdom_check"
```

Constraint allows only: `Fungi`, `Plantae`, `Animalia`, `Bacteria`, `Archaea`, `Protista`, `Viruses`, `Undesignated`.

MycoBank / observation upserts likely pass kingdom values outside this set (e.g. `"Fungi "` casing, `"Chromista"`, null handling).

**Fix options (pick one):**

- Normalize kingdom in `upsert_taxon()` / observation path to allowed enum before insert.
- Expand constraint if legitimate kingdoms missing (e.g. Chromista for some obs).
- Run `kingdom_backfill` **before** obs sync and map unknown → `Undesignated`.

---

#### 6. Kingdom backfill ineffective

**Evidence:** 100K+ MycoBank taxa but only **3** with `kingdom = 'Fungi'`; 23K `Undesignated`.

**Fix:** Ensure `kingdom_backfill` job completes; MycoBank ingest should set `kingdom = 'Fungi'` at insert time in `sync_mycobank_taxa_compat`.

---

#### 7. Secondary taxa sources return zero

| Source | Issue |
|--------|-------|
| **TheYeasts** | HTTP 200 but 0 species parsed — HTML/API structure drift |
| **Fusarium** | 0 species from species list page — scraper selectors stale |
| **Mushroom.World** | 7 errors per run, 0 inserts |

**Fix:** Per-source scraper audit in `mindex_etl/sources/` and `sync_*_taxa.py` jobs.

---

### P2 — Chemistry (PubChem / ChemSpider)

**Code path:** `sync_pubchem_compounds.py`, `sync_chemspider_compounds.py` → `bio.compound`, `bio.taxon_compound`.

**Blockers:** P0 #1 (grants) + P0 #2 (full compound schema).

**Env on VM (present):** `CHEMSPIDER_API_KEY`, `NCBI_API_KEY` in `.env`.

**After fix — manual smoke:**

```bash
docker exec mindex-etl python -m mindex_etl.jobs.sync_pubchem_compounds --max-results 50
docker exec mindex-etl python -m mindex_etl.jobs.sync_chemspider_compounds --limit 20
```

**API surface:** `mindex_api/routers/compounds.py` — will return empty until rows exist.

---

### P2 — Genetics (GenBank + FungiDB)

| Pipeline | Target table | Status |
|----------|--------------|--------|
| **FungiDB** | `bio.genome` | Blocked by grants; API fallback works (VEuPathDB 200) |
| **GenBank** | `bio.genetic_sequence` | Table missing + grants |

**After fix — manual smoke:**

```bash
docker exec mindex-etl python -m mindex_etl.jobs.sync_fungidb_genomes --max-pages 2
docker exec mindex-etl python -m mindex_etl.jobs.sync_genbank_genomes --max-pages 2
```

**API surface:** `mindex_api/routers/genetics.py` queries `bio.genetic_sequence`.

---

### P2 — Publications (PubMed / GBIF Literature / Semantic Scholar)

**Code path:** `mindex_etl/jobs/publications.py` → `core.publications` (correct table name).

**Status:** Table exists with full schema; **0 rows**. Job never scheduled to completion due to upstream failures.

**Note:** Default sources in `run()` are `["gbif", "semantic_scholar"]` — PubMed fetch exists but may need explicit `sources` list expansion.

**After fix — manual smoke:**

```bash
docker exec mindex-etl python -c "
import asyncio
from mindex_etl.jobs.publications import run_publications_etl
print(asyncio.run(run_publications_etl(max_pubs_per_source=10)))
"
```

---

### P2 — Images

| Layer | Mechanism | Count | Status |
|-------|-----------|------:|--------|
| **Taxon metadata** | iNat `default_photo` in `core.taxon.metadata` | 19,636 | ⚠️ External URLs only |
| **HQ pipeline** | `hq_media_ingestion.py` → `media.image` + derivatives on NAS | 0 | ❌ Never ran |
| **Taxon photos job** | `backfill_inat_taxon_photos.py` | — | ❌ Never ran |
| **Ancestry enrich** | `ancestry_sync.py` image enrichment | — | ❌ Never ran |

**Blockers:**

- P0 #1 — grants on `bio` / `media` (apply `20260603_ledger_grants.sql` for `media`)
- `media.image` table **exists** but empty
- HQ worker needs `LOCAL_DATA_DIR` / NAS writable (`/mnt/nas/mindex/...`)
- Audit script reference to `core.media_asset` is **stale** — canonical table is `media.image`

**After fix — manual smoke:**

```bash
docker exec mindex-etl python -m mindex_etl.jobs.hq_media_ingestion --limit 10 --dry-run
docker exec mindex-etl python -m mindex_etl.jobs.backfill_inat_taxon_photos --limit 50
```

---

## Schema migration checklist (VM 189)

Apply in order (verify each with `\d` / `SELECT count(*)`):

| Migration | Purpose | VM state (Jun 10) |
|-----------|---------|-------------------|
| `0007_compounds.sql` | Full `bio.compound` + taxon links | ❌ Stub only |
| `0012_genetics.sql` | `bio.genetic_sequence` | ❌ Missing |
| `0005_images.sql` / `0006_hq_media_enhancements.sql` | `media.image` HQ columns | ⚠️ `media.image` exists, 0 rows |
| `20260603_grants_bio_obs_core.sql` | `mindex` → bio/obs/core | ❌ Not applied |
| `20260603_ledger_grants.sql` | `mindex` → media/ledger | ❌ Not applied |

---

## Documentation drift

| Doc | Gap |
|-----|-----|
| `docs/ETL_SYNC_GUIDE.md` | Lists only taxa/obs sources; **no PubChem, ChemSpider, GenBank, publications, HQ media** |
| `docs/MINDEX_SYSTEM_STATUS.md` | Jan 2026 counts; partial Jun 10 note |
| `scripts/_audit_etl_full_vm189.py` | Queries `core.publication`, `core.media_asset` — **wrong names** (use `core.publications`, `media.image`) |

---

## Recommended remediation order

### Phase A — Unblock (same day)

1. Apply grants migrations (`20260603_*`) as DB superuser.
2. Apply `0007_compounds.sql` + `0012_genetics.sql`.
3. Rebuild/restart `mindex-etl` with numpy pin; confirm container **healthy**.
4. Fix `taxon_kingdom_check` / kingdom normalization for `inat_obs`.

### Phase B — Smoke each domain (same day)

5. Run manual pubchem, chemspider, genbank, fungidb, publications, hq_media jobs (limits 10–50).
6. Confirm non-zero counts in `bio.compound`, `bio.genetic_sequence`, `bio.genome`, `core.publications`, `media.image`.

### Phase C — Scheduler + sources (1–2 days)

7. Let scheduler run full cycle; monitor `docker logs mindex-etl` for 24h.
8. Fix theyeasts / fusarium / mushroom_world scrapers.
9. Complete kingdom backfill for MycoBank taxa.
10. Update `ETL_SYNC_GUIDE.md` and `MINDEX_SYSTEM_STATUS.md` with chemistry/genetics/images/publications sections.

### Phase D — Downstream (Mycosoft platform)

11. **Website** compound/genetics pages — depend on MINDEX API data (no UI change until data exists).
12. **MYCODAO tissue catalog** — relink `mindex_taxon_id` now that MycoBank taxa exist (search API).
13. **MAS registries** — update `SYSTEM_REGISTRY` / `API_CATALOG` when counts stabilize.

---

## Verification commands (operator)

```bash
# SSH VM 189
ssh mycosoft@192.168.0.189

# Counts (as mycosoft)
sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "
SELECT 'taxa', count(*) FROM core.taxon
UNION ALL SELECT 'compounds', count(*) FROM bio.compound
UNION ALL SELECT 'genomes', count(*) FROM bio.genome
UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence
UNION ALL SELECT 'publications', count(*) FROM core.publications
UNION ALL SELECT 'media_images', count(*) FROM media.image;
"

# ETL health
sudo docker ps --filter name=mindex-etl
sudo docker logs mindex-etl --tail 100 2>&1 | grep -iE 'error|failed|completed'
```

---

## Lessons learned

1. **Bootstrap migrations** (`20260502_all_life_universal.sql`) created hollow tables that let API boot but **silently broke** compound/genetics ETL expecting full schemas.
2. **Grant migrations** must be part of VM deploy checklist — ETL user `mindex` ≠ owner `mycosoft`.
3. **Scheduler serial failures** early in the job list prevent chemistry/genetics/images jobs from ever running; consider **isolating job failures** or running critical domains on separate schedules/containers.
4. **Kingdom constraint + MycoBank bulk** without kingdom assignment leaves observation sync and fungi filtering broken for downstream apps.

---

**Next doc:** `MINDEX_ETL_REMEDIATION_COMPLETE_JUN10_2026.md` (create after Phase A–B fixes applied).
