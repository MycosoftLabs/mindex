# MINDEX Taxa Remediation Complete — Jun 10, 2026

**Date:** 2026-06-10  
**Status:** Complete (P0–P2 applied; P1 bulk ingest running on VM 189)  
**Related:** [MINDEX_MISSING_TAXA_DIAGNOSIS_JUN10_2026.md](./MINDEX_MISSING_TAXA_DIAGNOSIS_JUN10_2026.md)

---

## Scope

Fix broken MINDEX taxonomy ETL on VM **192.168.0.189** so fungi taxa from **iNaturalist**, **GBIF**, and **MycoBank** ingest correctly. Impacts MYCODAO tissue catalog, website ancestry/search, NLM training, and all MAS consumers of `/api/mindex/taxa`.

---

## Delivered (code — MINDEX repo)

| Item | File(s) | Change |
|------|---------|--------|
| P0 MycoBank scheduler | `mindex_etl/jobs/run_all.py` | `sync_mycobank_taxa_compat` (accepts `max_pages`) |
| P0 GBIF schema | `mindex_etl/jobs/sync_gbif_occurrences.py` | PostGIS `location` geography (not `latitude`/`longitude`) |
| P0 iNat scrape path | `mindex_etl/sources/inat.py` | Non-fatal NAS save; taxa ingest continues if scrape write fails |
| P0/P2 domain defaults | `mindex_etl/config.py`, `docker-compose.yml` | `INAT_DOMAIN_MODE` / `GBIF_DOMAIN_MODE` default **fungi** |
| P2 scheduler pages | `docker-compose.yml` | `--max-pages 100` (was 20) |
| P1 GBIF species job | `mindex_etl/jobs/run_all.py`, `scheduler.py` | `gbif_complete` job wired (72h schedule) |
| P1 kingdom backfill | `mindex_etl/jobs/run_all.py`, `scheduler.py` | `kingdom_backfill` job wired (72h schedule) |
| Scheduler domain | `mindex_etl/scheduler.py` | Passes `domain_mode` from config to taxa/GBIF jobs |
| Civic job isolation | `mindex_etl/jobs/run_all.py` | Broad `except` so NumPy/SINE imports cannot break registry |
| VM numpy compat | `pyproject.toml`, `Dockerfile` | Pin `numpy>=1.26.4,<2.0` (VM 189 lacks x86-64-v2) |
| Deploy helpers | `scripts/deploy_taxa_fix_vm189.py`, `scripts/apply_taxa_remediation_vm_189.py` | SFTP + env + recreate ETL |

---

## VM 189 operations (executed)

1. Patched live files under `/home/mycosoft/mindex` (bind-mounted into `mindex-etl`).
2. Created `/mnt/nas/mindex/scrapes/work/{inat,mycobank}`.
3. Set `.env`: `INAT_DOMAIN_MODE=fungi`, `GBIF_DOMAIN_MODE=fungi`, `LOCAL_DATA_DIR=/mnt/nas/mindex/scrapes/work`.
4. Recreated `mindex-etl` with new scheduler command (`--max-pages 100`).
5. Fixed runtime: `pip install numpy==1.26.4` + `openpyxl` in ETL container (MycoBank MBList.xlsx).
6. Started background jobs: `sync_inat_taxa --domain-mode fungi`, `sync_gbif_complete`, `sync_mycobank_taxa`, `backfill_kingdom_lineage`.

---

## Verification (Jun 10, 2026 ~08:00 UTC)

| Check | Before | After (same session) |
|-------|--------|----------------------|
| `core.taxon` sources | `inat` only (10,166) | `inat` + `gbif` (e.g. 19,646 + 3,372 and rising) |
| GBIF job error | `column "latitude" does not exist` | `Synced 100 GBIF occurrences` (smoke test) |
| MycoBank job error | `unexpected keyword argument 'max_pages'` | MBList.zip download + XLSX parse started |
| ETL env | `INAT_DOMAIN_MODE=all` | `INAT_DOMAIN_MODE=fungi` |
| API `q=Pleurotus` | empty | Populates as iNat/GBIF fungi sync completes |

```bash
# Re-check anytime on 189
sudo docker exec mindex-postgres psql -U mindex -d mindex -c \
  "SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC;"
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:8000/api/mindex/taxa?q=Pleurotus%20ostreatus&limit=5"
sudo docker logs mindex-etl --tail 50
```

---

## Known follow-ups

| Item | Priority | Notes |
|------|----------|-------|
| **INAT_API_TOKEN** | High | Not in `.credentials.local`; add to VM `.env` to reduce 429 throttling on ancestry enrichment |
| **MycoBank full ingest** | In progress | ~545K rows; runs in background after MBList parse |
| **Image rebuild** | Medium | `docker compose build etl` on 189 after git push so numpy pin survives recreate |
| **MYCODAO tissue relink** | After ingest | Update `mindex_taxon_id` on Supabase samples when species appear in MINDEX |
| **MAS SYSTEM_REGISTRY** | Low | Update taxa counts after bulk jobs finish |

---

## Impact on downstream systems

| Consumer | Effect |
|----------|--------|
| **MYCODAO tissue catalog** | Can link `mindex_taxon_id` once species indexed |
| **Website ancestry / search** | Fungi taxa and GBIF/MycoBank sources repopulate |
| **NLM / training** | Richer taxonomy alongside existing 823K+ observations |
| **Fluid Search species widgets** | Queries like *Pleurotus ostreatus* resolve as ingest completes |

---

## Lessons learned

1. Status docs (19K–43K taxa) diverged from post-rebuild VM DB — always verify with live SQL.
2. GBIF ETL assumed flat lat/lng columns; canonical schema uses PostGIS `location`.
3. Optional ETL jobs (civic/SINE) must not break `create_job_registry()` on lean ETL images or old CPUs.
4. NumPy 2.x wheels require x86-64-v2; VM 189 needs numpy 1.26.x for openpyxl/MycoBank and SINE imports.
