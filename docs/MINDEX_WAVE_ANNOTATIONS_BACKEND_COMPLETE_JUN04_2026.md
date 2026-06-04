# MINDEX Wave Annotations + Human ID Backend — Complete (June 4, 2026)

**Date:** June 4, 2026  
**Status:** Complete  
**Git:** `b340465` on `main`  
**VM:** 192.168.0.189:8000  
**Related:** `docs/MINDEX_SINE_ACOUSTIC_VM_DEPLOY_COMPLETE_JUN04_2026.md`, `WEBSITE/website/docs/codex-handoffs/MINDEX_ACOUSTIC_CLASSIFIER_FRONTEND_PLAN_JUN04_2026.md`

---

## Delivered

| Area | Implementation |
|------|----------------|
| Migration | `migrations/20260604_library_wave_human_annotations_jun04_2026.sql` |
| Service | `mindex_api/services/library_annotations.py` |
| Routes | `mindex_api/routers/library.py` — wave + human identification CRUD (POST/GET) |
| Tests | `tests/test_library_annotations.py` |

### API routes (internal token)

- `POST /api/mindex/library/blobs/{id}/wave-annotation`
- `GET /api/mindex/library/blobs/{id}/wave-annotations`
- `POST /api/mindex/library/blobs/{id}/human-identification`
- `GET /api/mindex/library/blobs/{id}/human-identifications`
- `GET /api/mindex/library/blobs/{id}` now includes `wave_annotations`, `human_identifications`, `latest_human_identification`

Website BFF (Codex, not committed by Cursor):

- `POST /api/natureos/mindex/library/wave-annotation` → MINDEX wave-annotation
- `POST /api/natureos/mindex/library/human-identification` → MINDEX human-identification

---

## Verification (June 4, 2026)

| Check | Result |
|-------|--------|
| VM `POST .../wave-annotation` (test blob) | **200** `status: saved` |
| LAN `POST` via BFF `localhost:3010/.../wave-annotation` | **200** |
| LAN `POST` human-identification BFF | **200** |
| `http://localhost:3010/sensing/sine/player` | **200** |

Test blob: `a742bbd6-383d-4a7f-8945-e3c7d55c1982`

---

## Deploy

```bash
cd MINDEX/mindex && python scripts/_deploy_push_jun04_2026.py
```

Applies migration, restarts `mindex-api`, runs classify/analyze/wave smoke on VM.
