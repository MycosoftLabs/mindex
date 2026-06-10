# MINDEX Missing Taxa Diagnosis — Jun 10, 2026

**Date:** 2026-06-10  
**Status:** Remediated — see [MINDEX_TAXA_REMEDIATION_COMPLETE_JUN10_2026.md](./MINDEX_TAXA_REMEDIATION_COMPLETE_JUN10_2026.md)  
**VM:** 192.168.0.189 (MINDEX)  
**Symptom:** `/api/mindex/taxa?q=Pleurotus` (and most fungi) returns empty; tissue catalog could not link to MINDEX for 7 of 8 lab samples.

---

## Executive summary

MINDEX is **architected** to hold tens of thousands to hundreds of thousands of fungal taxa from **iNaturalist**, **GBIF**, and **MycoBank**, with ancestry metadata from iNat in `core.taxon.metadata`. **Documentation and status files still claim 19K–43K+ taxa with mixed GBIF/MycoBank/iNat sources.**

**Production reality on VM 189 (Jun 10, 2026):**

| Metric | Expected (docs) | Actual (VM 189) |
|--------|-----------------|-----------------|
| Total taxa in `core.taxon` | 19,387 – 43,621+ | **10,166** |
| Sources in DB | iNat + GBIF + MycoBank + … | **iNat only** (100%) |
| Fungi (`kingdom = 'Fungi'`) | Majority of catalog | **134** |
| Kingdom unset | — | **10,002** (98%) |
| MycoBank rows | ~150K–545K names | **0** |
| GBIF taxa rows | ~11K–150K species | **0** (GBIF job never succeeds) |
| Observations | 50K–500K+ | **823,972** (iNat all-life obs ingest works) |

**Root cause is not “ETL was never built.”** The ETL codebase is large and complete. The gap is **operational**: scheduled jobs are failing, misconfigured, or pointed at the wrong domain (all-life vs fungi), while status docs were never reconciled with the post–VM-rebuild database.

---

## Intended architecture (from repo docs)

### Stack (VM 189)

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  MINDEX API │◄───►│  ETL scheduler   │◄───►│  PostgreSQL     │
│  :8000      │     │  mindex-etl      │     │  core.taxon     │
└─────────────┘     │  APScheduler-ish │     │  obs.observation│
                    └────────┬─────────┘     └─────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         iNaturalist      GBIF          MycoBank
         (taxa + obs)   (occurrences   (~545K names)
                         + species)     FungiDB, PubChem, …
```

**Canonical references:**

| Doc | Claims |
|-----|--------|
| `docs/MINDEX_SYSTEM_STATUS.md` (Jan 2026) | 19,387 taxa; GBIF 11,164; iNat 4,357; scheduler every 4–24h |
| `docs/ETL_SYNC_GUIDE.md` | 50K+ iNat taxa, 150K MycoBank, full sync 2–3 days |
| `MINDEX_ETL_STATUS_FEB12_2026.md` | 43,621+ taxa; aggressive_runner on 189; GBIF active |
| `docs/ETL_SCHEDULER_ENABLEMENT_FEB10_2026.md` | `docker compose --profile etl up -d`; job table |
| `docs/ALL_LIFE_ETL_MAY02_2026.md` | Default `inat_domain_mode=all`, kingdom backfill job |

### ETL job registry (`mindex_etl/jobs/run_all.py`)

| Job | Source | Purpose |
|-----|--------|---------|
| `inat_taxa` | iNaturalist | Taxonomy pages under root taxon (fungi **47170** or life **1**) |
| `mycobank` | MycoBank | ~545K fungal names + synonyms |
| `gbif` | GBIF | **`sync_gbif_occurrences`** (not full species dump) |
| `inat_obs` | iNaturalist | Observations (creates taxa opportunistically) |
| `ancestry` | iNat enrich | Profile/images for existing species |
| `fungidb`, `theyeasts`, `pubchem`, … | Various | Secondary layers |

**Separate but not scheduled:** `sync_gbif_complete.py` — full GBIF fungal species search (150K+). **Not wired into `create_job_registry()`.**

### Data model

- **`core.taxon`** — canonical taxon row (`canonical_name`, `rank`, `source`, `kingdom`, `lineage`, `metadata` JSONB)
- **`core.taxon_external_id`** — maps `source` + external id → taxon UUID
- **`metadata`** for iNat includes `inat_id`, **`ancestry`** (slash-separated iNat path), `iconic_taxon_name`, `default_photo`, etc. — same shape ancestry explorer expects
- **`obs.observation`** — 823K rows on VM (working path)

---

## Live audit — VM 189 (Jun 10, 2026)

### Containers

| Container | Status |
|-----------|--------|
| `mindex-api` | Up, healthy |
| `mindex-postgres` | Up, healthy |
| `mindex-etl` | Up **6 days (unhealthy)** |
| `mindex-earth-sync` | Up unhealthy |

### Database counts

```sql
SELECT count(*) FROM core.taxon;                    -- 10166
SELECT source, count(*) FROM core.taxon GROUP BY source;  -- inat | 10166
SELECT kingdom, count(*) FROM core.taxon GROUP BY kingdom;
  -- (null) 10002 | Fungi 134 | Plantae 30
SELECT count(*) FROM obs.observation;               -- 823972
```

**Sample fungi present:** *Trametes versicolor*, *Conocybe*, *Agaricales*, etc.  
**Missing (examples):** *Pleurotus ostreatus*, *Saccharomyces cerevisiae*, *Cantharellus cibarius*, *Stachybotrys chartarum*, *Hericium erinaceus*.

### ETL configuration (from `docker inspect mindex-etl`)

```
command: python -m mindex_etl.scheduler --interval 5 --max-pages 20
INAT_DOMAIN_MODE=all
GBIF_DOMAIN_MODE=all
INAT_API_TOKEN=          # EMPTY
DATABASE_URL=postgresql://mindex:***@db:5432/mindex
```

**Implications:**

1. **`--max-pages 20`** caps each scheduled pass to ~2,000 iNat taxa pages before rotating to other jobs — far below doc expectations (100+ pages, full sync).
2. **`INAT_DOMAIN_MODE=all`** crawls **all life** from iNat root taxon `1`, not Fungi `47170` — explains birds/insects in ETL logs and only **134** rows with `kingdom='Fungi'`.
3. **No iNat API token** → anonymous rate limits → pervasive **`429 normal_throttling`** in ancestry enrichment logs.

### NAS / scrape path

- Host: `//192.168.0.105/.../mindex` mounted at `/mnt/nas/mindex` ✅  
- Container: same mount ✅  
- **`/mnt/nas/mindex/scrapes` does not exist** inside container ❌  

**`inat_taxa` job error (repeating every ~8 min):**

```
Job inat_taxa failed: [Errno 2] No such file or directory: '/mnt/nas/mindex/scrapes'
```

Occurs at end of `iter_inat_taxa` when `save_to_local=True` tries to write scrape JSON (`mindex_etl/sources/inat.py`).

### Scheduled job failures (from `docker logs mindex-etl`)

| Job | Error | Effect |
|-----|-------|--------|
| **mycobank** | `sync_mycobank_taxa() got an unexpected keyword argument 'max_pages'` | **Every run fails.** Scheduler passes `max_pages`; registry uses `sync_mycobank_taxa` instead of `sync_mycobank_taxa_compat`. **Zero MycoBank ingest.** |
| **gbif** | `column "latitude" of relation "observation" does not exist` | **Every run fails.** Schema drift: ETL expects `obs.observation.latitude`; DB column missing or renamed. **No GBIF occurrence ingest.** |
| **inat_taxa** | Missing `/mnt/nas/mindex/scrapes` | Taxa crawl may fetch pages then **crash on save**; net growth stalled. |
| **ancestry** | iNat `429` throttling | Enrichment for existing species mostly fails. |

### Why observations grew but taxa did not

`inat_obs` (all-life, rolling backfill) **succeeds** and has created **823,972** observation rows. Taxa-heavy jobs (**mycobank**, **gbif**, healthy **inat_taxa** bulk) are broken. The DB looks “full” on observations but is **taxonomically empty** for product use cases (search, ancestry, tissue linking).

---

## Why `/api/mindex/taxa` searches miss fungi

1. **Species never ingested** — not in `core.taxon` at all.
2. **`kingdom=Fungi` filter** — only **134** rows pass; most iNat rows have `kingdom IS NULL` despite `metadata.iconic_taxon_name = 'Fungi'` on some.
3. **`backfill_kingdom_lineage` not run** on VM after May 2026 all-life migration (`docs/ALL_LIFE_ETL_MAY02_2026.md` documents the job; no evidence of completion on 189).
4. **Doc/API drift** — Jan 2026 status doc describes stats from a **different database epoch** (likely pre-rebuild local Docker or another host).

---

## Documentation vs reality (doc drift)

| Document | Stated | VM 189 Jun 10 |
|----------|--------|---------------|
| `MINDEX_SYSTEM_STATUS.md` | 19,387 taxa, GBIF 11K | 10,166 iNat-only |
| `MINDEX_ETL_STATUS_FEB12_2026.md` | 43,621+ taxa, GBIF active | GBIF job failing |
| `ETL_SYNC_GUIDE.md` | MycoBank 150K+ | 0 MycoBank |
| `MINDEX_VM_DEPLOYMENT_STATUS_FEB04_2026.md` | Fresh VM, memory/ledger schemas | Does not mention fungal ETL completion |

**Conclusion:** Status docs are **stale** relative to the Feb–Jun 2026 VM rebuild, NAS migration, and all-life mode switch. They must not be used for capacity planning until refreshed from live SQL.

---

## Remediation priority (recommended)

### P0 — Unblock taxa ingest (same day)

1. **Fix MycoBank scheduler wiring** — point `run_func` to `sync_mycobank_taxa_compat` in `run_all.py` (compat wrapper already exists).
2. **Create scrape dir** — `mkdir -p /mnt/nas/mindex/scrapes/work` on NAS (host + visible in ETL container).
3. **Set `INAT_API_TOKEN`** in VM `.env` (from credentials store) and restart `mindex-etl`.
4. **Fix GBIF observation schema** — align `obs.observation` columns with `sync_gbif_occurrences.py` (migration or code fix for `latitude`/`longitude`).
5. **Set `INAT_DOMAIN_MODE=fungi`** (or run dedicated fungi pass) until MycoBank backfill completes.

### P1 — Restore fungal mass (hours–days)

6. **One-shot MycoBank sync** — `python -m mindex_etl.jobs.sync_mycobank_taxa` (hours; ~545K names).
7. **One-shot GBIF fungi species** — `python -m mindex_etl.jobs.sync_gbif_complete` (not in scheduler today).
8. **iNat fungi taxa full sync** — `python -m mindex_etl.jobs.sync_inat_taxa --domain-mode fungi` with high `--max-pages` or no limit.
9. **Run `backfill_kingdom_lineage`** after bulk ingest.

### P2 — Operations hygiene

10. Raise scheduler `--max-pages` (e.g. 100+) or run `run_all --full` via `etl-init` profile once.
11. Mark `mindex-etl` healthy only when jobs succeed; alert on repeated job failures.
12. **Refresh** `MINDEX_SYSTEM_STATUS.md` and MAS `SYSTEM_REGISTRY` taxa counts from live `SELECT source, count(*) FROM core.taxon`.
13. Wire **`sync_gbif_complete`** into scheduler or document it as manual-only clearly.

---

## Impact on downstream systems

| Consumer | Impact |
|----------|--------|
| **MYCODAO tissue catalog** | `mindex_taxon_id` null for most samples; ancestry taken from iNat API directly |
| **Website ancestry explorer** | Sparse fungi tree; relies on MINDEX taxa + metadata.ancestry |
| **Fluid Search / species widgets** | Empty or fallback for unindexed names |
| **NLM / training pipelines** | Observation-rich, taxonomy-poor |

---

## Verification commands (after fixes)

```bash
# On VM 189
docker exec mindex-postgres psql -U mindex -d mindex -c \
  "SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC;"

curl -s -H "X-API-Key: $KEY" \
  "http://127.0.0.1:8000/api/mindex/taxa?q=Pleurotus%20ostreatus&limit=5"

docker logs mindex-etl --tail 50 | grep -E "Job (mycobank|gbif|inat_taxa)"
```

**Success criteria:** MycoBank + GBIF sources appear in `core.taxon`; fungi search returns lab species; `kingdom` populated for >90% of fungal rows; scheduler logs show completed jobs without repeating errors.

---

## Related files

- `mindex_etl/jobs/run_all.py` — job registry (MycoBank wiring bug)
- `mindex_etl/jobs/sync_mycobank_taxa.py` — `sync_mycobank_taxa_compat`
- `mindex_etl/jobs/sync_gbif_occurrences.py` vs `sync_gbif_complete.py`
- `mindex_etl/config.py` — `inat_domain_mode` default `all`
- `mindex_etl/sources/inat.py` — ancestry in `metadata`; NAS save path
- `docker-compose.yml` — ETL command `--max-pages 20`
- `docs/ETL_SYNC_GUIDE.md`, `docs/MINDEX_SYSTEM_STATUS.md` — **stale counts**
