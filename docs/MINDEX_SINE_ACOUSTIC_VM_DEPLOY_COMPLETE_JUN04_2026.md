# MINDEX SINE Acoustic Library — VM Deploy Complete (June 4, 2026)

**Date:** June 4, 2026  
**Status:** Complete  
**VM:** `192.168.0.189:8000` (MINDEX API)  
**Git:** `MycosoftLabs/mindex` @ `a0e391a` on `main`  
**Related:** `docs/SINE_ACOUSTIC_BACKEND_MAY27_2026.md`, `docs/ACOUSTIC_CLASSIFIER_SCOPE_MAY27_2026.md`, `docs/MINDEX_LIBRARY_NAS_MOUNT_MAY27_2026.md`

**Website / frontend:** Codex owns `WEBSITE/website` (see `docs/codex-handoffs/MINDEX_ACOUSTIC_CLASSIFIER_FRONTEND_PLAN_JUN04_2026.md`). Do not mix chemistry/DNA tracks into this acoustic pass.

---

## Summary

MINDEX VM **189** was repaired, the full **Library + SINE acoustic** stack was committed and pushed to GitHub, deployed with migrations, and verified end-to-end for health, catalog, classify, and analyze. The dev website BFF on port **3010** was smoke-tested against **189** (website push is separate).

---

## Root causes fixed

| Issue | Symptom | Fix |
|-------|---------|-----|
| Wrong DB host | `health` → `db: error`; library **503** | Set `MINDEX_DB_HOST=mindex-postgres` in VM `.env` (app uses `MINDEX_DB_*`, not `DATABASE_URL` alone) |
| Stale internal token | Website BFF **401** on library | Sync `MINDEX_INTERNAL_TOKEN` in dev `.env.local` to VM `MINDEX_INTERNAL_TOKENS` |
| Code not on VM | `POST .../classify` **404** | Pushed `a0e391a` + `git pull` on 189 |
| asyncpg SQL cast | `POST .../analyze` **500** | Replace `:meta::jsonb` with `CAST(:meta AS jsonb)` in `mindex_api/routers/sine_acoustic.py` |

---

## Git deliverable (`a0e391a`)

**Commit message:** `feat(mindex): SINE acoustic library, classifier, and VM ops scripts`

**Included (high level):**

- `mindex_api/routers/library.py` — catalog, stream, classify
- `mindex_api/routers/sine_acoustic.py` — status, analyze, visualisation, analysis
- `mindex_api/services/sine_acoustic/` — detector pipeline + `classifier.py` + `event_views.py`
- `mindex_etl/library/` — NAS paths, MBARI/ESC50/HF ingest helpers
- Migrations: `20260527_library_acoustic`, `20260604_library_blob_labels`, `20260605_sine_acoustic_stack`
- Tests: `tests/test_acoustic_event_views.py`, `tests/test_sine_acoustic_pipeline.py`
- Ops scripts: `_fix_mindex_db_host_may27.py`, `_restore_mindex_backend_may27.py`, `_deploy_sine_acoustic_may27_2026.py`, etc.
- Docs: acoustic/SINE/NAS scope and stack completion (May 27 titles retained)

**Not in repo:** `.credentials.local`, root `_*.py` one-off diagnostics, `docs/*_VERIFY*.json` artifacts.

---

## VM deploy procedure (June 4, 2026)

```bash
cd /home/mycosoft/mindex
git fetch origin && git reset --hard origin/main   # → a0e391a
# Ensure .env: MINDEX_DB_HOST=mindex-postgres
# Apply migrations (idempotent) via docker exec mindex-postgres psql
docker exec mindex-api pip install -q numpy scipy soundfile auditok
docker restart mindex-api
```

**Local helper:** `scripts/_deploy_push_jun04_2026.py` (pull, migrate, restart, curl smoke).

**Container:** `mindex-api` on `0.0.0.0:8000`, NAS mount `/mnt/nas/mindex`, postgres service `mindex-postgres`.

---

## Verification (June 4, 2026)

| Check | Result |
|-------|--------|
| `GET /api/mindex/health` | **200**, `"db":"ok"` |
| `GET /api/mindex/library/blobs?category=acoustic&limit=5` | **200**, `total`: **2180** |
| `GET /api/mindex/library/storage` | **200**, `remote_nas`: true |
| `GET /api/mindex/sine/status` | **200**, 7 detectors |
| `POST /api/mindex/library/blobs/{id}/classify?detectors=frequency_fft` | **200** (small test blob) |
| `POST /api/mindex/sine/blobs/{id}/analyze?detectors=frequency_fft` | **200**, 12 frequency events (small blob) |
| LAN from dev PC (`192.168.0.189:8000`) | health, blobs, sine status **200** |
| BFF `localhost:3010/api/mindex/sine/status` | **200** (when dev server running) |

**Test blob ID (small WAV):** `a742bbd6-383d-4a7f-8945-e3c7d55c1982`

---

## API surface (acoustic)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/mindex/library/blobs` | Paginated acoustic catalog |
| GET | `/api/mindex/library/blobs/{id}` | Blob + latest classification fields |
| GET | `/api/mindex/library/blobs/{id}/stream` | NAS file stream |
| POST | `/api/mindex/library/blobs/{id}/classify` | Run classifier, Library-shaped JSON |
| GET | `/api/mindex/sine/status` | Stack health + counts |
| POST | `/api/mindex/sine/blobs/{id}/analyze` | Full analysis + DB persistence |
| GET | `/api/mindex/sine/blobs/{id}/visualisation` | Waveform/spectrogram JSON |
| GET | `/api/mindex/sine/blobs/{id}/analysis` | Last `analysis_run` (**404** if never analyzed) |

Auth: `X-Internal-Token` (first value from `MINDEX_INTERNAL_TOKENS` on VM).

---

## Known limits

- **Large MBARI WAVs** (~500 MB) can timeout on analyze/visualisation; frontend should auto-select smallest clip and skip auto preload for files **> 75 MB** (Codex plan).
- **Duplicate blob IDs** in DB pages: BFF de-dupe on website; backend may still return duplicates until ETL dedupes.
- **Bird/UAV detectors** are heuristics in this pass, not full upstream ONNX clones.

---

## Follow-up

| Owner | Task |
|-------|------|
| **Codex** | Website: commit/push frontend + BFF (`MINDEX_ACOUSTIC_CLASSIFIER_FRONTEND_PLAN_JUN04_2026.md`) |
| **Other agent** | MycoBrain — out of scope for this doc |
| **Ops** | Optional: commit `scripts/_deploy_push_jun04_2026.py` on next MINDEX commit |

---

## Lessons learned

1. Always set **`MINDEX_DB_HOST`** for Dockerized API (not only `DATABASE_URL`).
2. After VM code changes, prefer **`git pull` + restart** over SFTP once `main` is current.
3. SQLAlchemy `text()` + asyncpg: use **`CAST(:param AS jsonb)`**, not `:param::jsonb`.
