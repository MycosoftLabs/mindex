# Codex Handoff — Earth Simulator & Website Apps (Post-MINDEX ETL Remediation)

**Date:** June 10, 2026  
**Status:** Handoff ready — MINDEX Phases A–C **complete** on VM 189 (see `MINDEX_ETL_REMEDIATION_COMPLETE_JUN10_2026.md`)  
**Audience:** Codex (or any agent) working on **WEBSITE/website** apps that consume MINDEX  
**Related:** `MINDEX_ETL_FULL_AUDIT_JUN10_2026.md`, `MINDEX_TAXA_REMEDIATION_COMPLETE_JUN10_2026.md`

---

## Executive summary

MINDEX taxonomy is **production-usable** (**414K+** taxa with `kingdom='Fungi'`). Chemistry, genetics, and publications are **live** on VM 189; HQ media schema is ready (ingest dry-run OK, 0 rows until full job runs).

**VM 189 counts (Jun 10, 2026):** compounds **93**, sequences **200**, publications **120**, genomes **331**, `media.image` **0** (pipeline ready).

**Codex should:**

1. Wire website apps to **real MINDEX APIs** (never mock arrays).
2. Use real compound/sequence/publication endpoints — show empty states only when API returns zero rows, not mock fallbacks.
3. Prefer **website BFF proxies** (`/api/mindex/*`) from the browser; server routes use `resolveMindexServerBaseUrl()` → `http://192.168.0.189:8000`.
4. **Do not** change marketing/copy/layout unless Morgan explicitly requests it (website hard rule).

---

## Infrastructure (canonical)

| System | Host | Port | Role |
|--------|------|------|------|
| **MINDEX API** | `192.168.0.189` | **8000** | Taxa, observations, compounds, genetics, earth/CREP layers |
| **MAS** | `192.168.0.188` | **8001** | Agents, devices, voice orchestration |
| **Website dev** | local PC | **3010** | `npm run dev:next-only` |
| **Website prod** | `192.168.0.187` | **3000** | Docker + Cloudflare |

### Website `.env.local` (dev PC)

```env
MINDEX_API_URL=http://192.168.0.189:8000
MINDEX_API_BASE_URL=http://192.168.0.189:8000
NEXT_PUBLIC_MINDEX_API_URL=http://192.168.0.189:8000
MAS_API_URL=http://192.168.0.188:8001
NEXT_PUBLIC_MAS_API_URL=http://192.168.0.188:8001
```

Server-side code **must not** use `http://localhost:8000` for MINDEX on the Sandbox VM unless `ALLOW_LOOPBACK_MINDEX=1`. See `WEBSITE/website/lib/mindex-base-url.ts`.

---

## MINDEX remediation status (Jun 10, 2026 ~20:25 UTC)

### Done on VM 189

| Item | Status |
|------|--------|
| `bio` schema grants for `mindex` role | ✅ **64** grants |
| `pg_trgm` extension | ✅ |
| `core.migration_log` table | ✅ |
| Full `bio.compound` schema (`0007_compounds.sql`) | ✅ |
| `bio.genetic_sequence` (`0012_genetics.sql`) | ✅ |
| Kingdom normalization code (`taxon_canonicalizer.py`, `mycobank.py`) | ✅ pushed to VM |
| MycoBank kingdom backfill | ✅ **~366K** `Fungi` (was 3) |
| MINDEX API health | ✅ `{"status":"healthy"}` |
| Observations in PostGIS | ✅ **~834K** rows |
| FungiDB genomes | ✅ **331** in `bio.genome` |

### Still empty / in progress (ETL must run)

| Domain | Table | Count | Next step |
|--------|-------|------:|-----------|
| Compounds | `bio.compound` | **0** | Run pubchem/chemspider smokes on VM |
| GenBank sequences | `bio.genetic_sequence` | **0** | Run genbank smoke |
| Publications | `core.publications` | **0** | Run publications ETL |
| HQ images | `media.image` | **0** | Run `hq_media_ingestion` after obs pipeline stable |
| ETL container health | `mindex-etl` | **unhealthy** | NumPy pin + rebuild (see audit doc) |

### Run on VM 189 (ops — not website code)

```bash
# From dev PC (MINDEX repo):
python scripts/_apply_compound_genetics_migrations_vm189.py   # if tables missing
python scripts/apply_etl_remediation_phases_abc_vm189.py      # full A–C (PowerShell: use ; not &&)

# Manual smokes (SSH to 189):
sudo docker exec mindex-etl python -m mindex_etl.jobs.sync_pubchem_compounds --max-results 50
sudo docker exec mindex-etl python -m mindex_etl.jobs.sync_chemspider_compounds --limit 20
sudo docker exec mindex-etl python -m mindex_etl.jobs.sync_genbank_genomes --max-pages 2
sudo docker exec mindex-etl python -c "import asyncio; from mindex_etl.jobs.publications import run_publications_etl; print(asyncio.run(run_publications_etl(max_pubs_per_source=10)))"

# Verify:
python scripts/_quick_vm189_counts.py
python scripts/_audit_etl_full_vm189.py
```

---

## Earth Simulator — architecture for Codex

**Route:** `/natureos/earth-simulator`  
**Boot config:** `WEBSITE/website/lib/crep/earth-simulator-boot.ts`

### Data flow (fungi / species layer)

```
Earth Simulator map (client)
  → mindexFetch("species", bounds)     [lib/crep/mindex-cache-client.ts]
  → GET /api/mindex/proxy/species?lat_min&lat_max&lng_min&lng_max&limit=
  → MINDEX earth/bbox PostGIS OR /api/crep/fungal fallback
  → ingestBatchToMINDEX() fire-and-forget [lib/crep/species-catalog.ts]
  → POST /api/mindex/proxy/species (persistence)
```

### Layer IDs Codex should know

| Boot constant | Purpose |
|---------------|---------|
| `EARTH_SIM_INSTANT_LIVE_LAYER_IDS` | `fungi`, `biodiversity`, MycoBrain device layers — **on at first paint** |
| `EARTH_SIM_DEFAULT_FUNGAL_LAYER_ID` | `fungalAtlasECM` — default fungal raster |
| `EARTH_SIM_EVENT_LAYER_IDS` | earthquakes, volcanoes, wildfires, storms, etc. |
| `EARTH_SIM_INSTANT_INFRA_LINE_IDS` | submarine cables, transmission lines |

### What to fix / verify in Earth Simulator

1. **Species markers empty:** Check `/api/mindex/proxy/species` response — if MINDEX bbox returns 0, proxy may fall back to live iNaturalist (`dataSource: "live_inaturalist_proxy_fallback"`). That is **real data**, not mock; ensure UI renders fallback entities.
2. **Kingdom filter:** MINDEX taxa now have `kingdom='Fungi'` for MycoBank; observation sync (`inat_obs`) may still hit `taxon_kingdom_check` for non-fungal iconic taxa — filter client-side to Fungi where appropriate.
3. **No mock observations:** Remove any hardcoded fungal marker arrays; use `mindexFetch` or proxy only.
4. **MINDEX down:** Show empty state + error banner; do **not** inject sample GeoJSON.
5. **Staged boot:** `NEXT_PUBLIC_EARTH_SIM_STAGED_BOOT=0` reverts to legacy mount (for debugging only).

### Key files (Earth Simulator + CREP)

| File | Role |
|------|------|
| `lib/crep/mindex-cache-client.ts` | Client fetch wrapper for all CREP layers |
| `app/api/mindex/proxy/[source]/route.ts` | BFF proxy; species → MINDEX fungal/earth endpoints |
| `lib/crep/species-catalog.ts` | Observation persistence to MINDEX |
| `lib/crep/mindex-integration.ts` | CREP ↔ MINDEX entity mapping |
| `lib/crep/earth-simulator-boot.ts` | Layer boot order and bbox defaults |
| `lib/mindex-base-url.ts` | Server-side MINDEX URL resolution |

---

## Website ↔ MINDEX API map

### BFF (browser-safe)

| Website path | MINDEX upstream (typical) |
|--------------|---------------------------|
| `/api/mindex/[[...path]]` | `/api/mindex/*` catch-all proxy |
| `/api/mindex/taxa` | Taxa search/list |
| `/api/mindex/proxy/species` | Earth/fungal bbox + iNat fallback |
| `/api/mindex/proxy/earthquakes` … | CREP earth layers |
| `/api/compounds/species/[id]` | `/api/mindex/compounds/for-taxon/{id}` |
| `/api/ancestry/[id]/publications` | `/api/mindex/all-life/taxa/{id}/publications` |
| `/api/worldview/v1/search` | Multi-type search (species, compounds, genetics, research, earth) |

### Direct MINDEX API (server-side or docs)

Base: `http://192.168.0.189:8000`

| Endpoint area | Router | Use when |
|---------------|--------|----------|
| `/api/mindex/taxa` | taxa | Species explorer, ancestry |
| `/api/mindex/compounds` | compounds | Compounds page, compound simulator |
| `/api/mindex/genetics` | genetics | Genetics tools, search widgets |
| `/api/crep/fungal` | earth/crep | Fungal observations bbox |
| `/api/mindex/all-life/taxa/{id}/publications` | all-life | Ancestry publications tab |
| `/health` | — | Regression checks |

Config helper: `WEBSITE/website/lib/config/api-urls.ts` → `API_URLS.MINDEX`, `COMPOUNDS`, `GENETICS`.

---

## Per-app Codex tasks (prioritized)

### P0 — Data correctness (all apps)

- [ ] Audit for **mock/fake arrays**; replace with MINDEX fetch + empty states.
- [ ] Ensure server routes use `resolveMindexServerBaseUrl()` or `API_URLS.MINDEX`, not hardcoded localhost.
- [ ] When API returns `[]` or 503, show **"No data available"** / skeleton — never placeholder species.

### P1 — Earth Simulator + CREP

- [ ] Verify `fungi` / `biodiversity` layers populate at US bbox (`EARTH_SIM_US_BBOX`) on localhost:3010.
- [ ] Log `dataSource` from proxy response; surface in dev-only debug panel if empty.
- [ ] Confirm `ingestBatchToMINDEX` does not block render (already fire-and-forget).
- [ ] CREP defense portal (`components/defense/defense-portal-v2.tsx`) — same proxy pattern as Earth Simulator.

### P2 — Compounds & ancestry (blocked until ETL populates)

- [ ] `/compounds`, `/natureos/compounds` — wire to `/api/mindex/compounds` when `bio.compound` > 0.
- [ ] `app/api/compounds/route.ts` still uses Mongo `find("compounds")` for some paths — **migrate to MINDEX** for production truth.
- [ ] Ancestry explorer publications tab — already proxies MINDEX; show empty until `core.publications` fills.

### P3 — Genetics & search

- [ ] Genetics tools: `/api/mindex/genetics` via BFF; empty until GenBank ETL runs.
- [ ] Fluid search / worldview search (`/api/worldview/v1/search`) — handle zero compounds/genetics gracefully in widgets.

### P4 — Other NatureOS apps (same rules)

| App route | MINDEX dependency |
|-----------|-------------------|
| `/natureos/mindex/explorer` | Taxa + observations heatmap |
| `/ancestry/explorer` | Taxa tree, publications, compounds |
| `/compounds` | `bio.compound` |
| `/natureos/genetics` | `bio.genetic_sequence` |
| `/natureos/virtual-petri-dish` | Species list from MINDEX taxa |
| Petri dish / mushroom sim | Species metadata |

---

## Empty-state UX pattern (required)

```tsx
// GOOD — real API, empty result
const res = await fetch(`/api/mindex/proxy/species?${params}`);
if (!res.ok) return <ErrorState message="MINDEX unavailable" />;
const data = await res.json();
if (!data.entities?.length) return <EmptyState message="No fungal observations in this area" />;

// BAD — never do this
const markers = [{ id: 1, name: "Demo Mushroom", lat: 32.7, lng: -117.1 }];
```

---

## Verification checklist (Codex)

**Local dev (3010):**

```powershell
# MINDEX reachable from dev machine
Invoke-RestMethod http://192.168.0.189:8000/health

# Species proxy (replace bounds as needed)
Invoke-RestMethod "http://localhost:3010/api/mindex/proxy/species?lat_min=32&lat_max=34&lng_min=-118&lng_max=-116&limit=10"

# Taxa
Invoke-RestMethod "http://localhost:3010/api/mindex/taxa?limit=5"
```

**After MINDEX ETL smokes:**

```powershell
Invoke-RestMethod "http://192.168.0.189:8000/api/mindex/compounds?limit=5"
Invoke-RestMethod "http://192.168.0.189:8000/api/mindex/genetics?limit=5"
```

**Earth Simulator:** Open `http://localhost:3010/natureos/earth-simulator`, zoom to San Diego bbox, confirm fungal markers or explicit empty state (not fake data).

---

## Known issues (do not “fix” in UI with mocks)

| Issue | Impact on website | Backend fix |
|-------|-------------------|-------------|
| `bio.compound` count 0 | Compounds pages/widgets empty | Run pubchem/chemspider ETL |
| `bio.genetic_sequence` count 0 | Genetics search empty | Run genbank ETL |
| `core.publications` count 0 | Ancestry pubs tab empty | Run publications ETL |
| `mindex-etl` unhealthy | Slow/stalled ingestion | NumPy 1.26.4 rebuild on VM |
| `Undesignated` kingdom ~204K | Some taxa lack kingdom in filters | `kingdom_backfill` job (running) |
| `app/api/compounds/route.ts` Mongo path | Stale/non-MINDEX data if used | Point to MINDEX proxy |

---

## Repos and paths

| Repo | Path |
|------|------|
| MINDEX | `D:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex` |
| Website | `D:\Users\admin2\Desktop\MYCOSOFT\CODE\WEBSITE\website` |
| MAS (creds) | `D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local` |

**VM 189 repo:** `/home/mycosoft/mindex`

---

## Suggested Codex session prompt

Copy into Codex:

> Read `MINDEX/mindex/docs/CODEX_HANDOFF_EARTH_SIMULATOR_WEBSITE_APPS_JUN10_2026.md`.  
> Scope: WEBSITE/website only. Fix Earth Simulator and CREP apps to use real MINDEX data via `/api/mindex/proxy/*` and BFF routes. No mock data. Handle empty compounds/genetics/publications until ETL fills VM 189. Dev server port 3010. Do not change marketing pages or copy unless specified. Verify with health + proxy calls before claiming done.

---

## Changelog (this handoff)

| Time (UTC) | Change |
|------------|--------|
| Jun 10 | Kingdom backfill → ~366K Fungi |
| Jun 10 | Applied `0007_compounds`, `0012_genetics`, grants, `pg_trgm` |
| Jun 10 | FungiDB genomes → 331 |
| Jun 10 | Compounds/sequences/publications still 0 — ETL smokes pending |

**Owner for MINDEX ETL:** Cursor/MAS agents via `scripts/apply_etl_remediation_phases_abc_vm189.py`  
**Owner for website:** Codex per Morgan directive
