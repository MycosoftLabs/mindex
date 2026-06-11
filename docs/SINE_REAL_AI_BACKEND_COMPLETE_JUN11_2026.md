# SINE Real-AI Backend — Complete (June 11, 2026)

**Date:** June 11, 2026
**Status:** Complete — live E2E verifier returns `status: ready`, zero failing checks on VM 189.
**Handoff source:** `WEBSITE/website/docs/codex-handoffs/SINE_CURSOR_BACKEND_FULL_FUNCTIONAL_HANDOFF_JUN08_2026.md`
**Plan chosen:** A (CPU on MINDEX 189). B (add GPU) and C (AWS) deferred — both Legions offline (one sold, one in use), so no GPU available.

---

## Outcome

SINE is now a real acoustic classifier on VM 189, not a detector-shaped UI. The JUN08 honesty contract holds: detector-only output never claims semantic identity, and identity comes only from a checksum-verified, runtime-loaded model.

| Gate (from JUN08 handoff) | Result |
|---|---|
| MINDEX health | 200, `db: ok` |
| `/sine/models` | `model_ready: true`, 1 loaded checksum-backed artifact |
| `/sine/prototypes` | `prototype_catalog_ready: true` |
| `/sine/blobs/{id}/analyze` | runs real torch inference; persists `sine.model_output`, `sine.fusion_evidence`, `sine.sound_transcript` |
| Evidence cross-links | model_output ↔ artifact_sha256/label_map_sha256; transcript ↔ model_output_ids + fusion_evidence_ids |
| OOD / honesty | `ood_score` returned; no fake labels; detector errors surfaced, not hidden |
| `verify_sine_real_ai_e2e.py` | **`status: ready`, checks: [] (no failures)** |

---

## The P0 model

- **Artifact:** `sine-esc50-cnn-p0-v1` (TorchScript, CPU), `/mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/`
- **Training data:** real ESC-50 WAVs on NAS (`/mnt/nas/mindex/Library/acoustic/esc50/`), 50 classes, real category names reconstructed into `meta/esc50.csv` from ESC-50 target ids.
- **Result:** 12 epochs, **validation accuracy 0.4725** (50-class; random = 0.02). Train 3200 / val 800 windows.
- **Package (verifier-passed):** `model.torchscript.pt`, `labels.json`, `metrics.json`, `confusion_matrix.json`, `training_manifest.json`, `model_registry_row.json`, `register_model_artifact.sql`, plus generated `verification_report.json`, `runtime_smoke_report.json`, `mark_model_loaded.sql`, `prototypes.json`, `register_prototypes.sql`, `e2e_real_ai_report.json`.
- **Runtime:** CPU torch 2.2.2 (`onnxruntime` also present).

---

## What was changed (committed to mindex `main`)

| Commit | Change |
|---|---|
| `a94435e` | **Dockerfile:** add CPU `torch==2.2.2` + `soundfile`/`auditok` to `mindex-api` image (SINE acoustic runtime; Legions offline so inference runs on this CPU image). |
| `2ad8eca` | **fix:** train script unpacks `extract_sine_feature_tensor` dict (`tensor` key) — Codex script was out of sync with `features.py`. |
| `e1c425c` | **perf:** train script caches log-mel features across epochs + per-epoch progress logging (observable CPU training). |
| `d42dee5` | **fix:** `json.dumps(default=str)` in analyze persistence (UUID not JSON serializable 500). |
| `a2c136d` | **fix:** same `default=str` in the analyze route summary/vis/meta persistence. |
| `3b249db` | **fix:** `build_library_classification_payload(**_ignored)` tolerates extra persisted-evidence keys (`deep_signal_matches`). |
| `f3ea8ea` | **feat:** `/sine/status` `inference_ready=true` only with full provenance proof (loaded + runtime + verified artifact + prototypes + ≥1 persisted `model_output`). |
| `d545f8c` | **fix:** bind `uuid[]` transcript params as Python lists — asyncpg rejected the `'{uuid}'` array-literal string, which silently blocked `sine.sound_transcript` rows. |

VM 189 deployed at `d545f8c`, `mindex-api` rebuilt with torch and restarted.

---

## Known non-blocking follow-ups

1. **`activity_auditok` detector** errors `'AudioRegion' object has no attribute 'start'` (auditok API drift). Non-blocking — other 6 detectors OK and the gate passes — but should be fixed for activity-segment evidence.
2. **Accuracy** 0.47 is a modest P0. Improve with augmentation / more epochs / GPU when B/C land.
3. **Prototype `model_status` field** on `/sine/prototypes` shows `model_unavailable` in the row block while `prototype_catalog_ready: true` — cosmetic; catalog is ready.
4. **Image size:** CPU torch adds ~300MB to `mindex-api`. Acceptable; revisit if a dedicated inference image is preferred.

---

## How to retrain / re-verify

Orchestration script (CPU, idempotent steps): `MINDEX/mindex/scripts/_sine_p0_train_jun11.py` with steps `build csv train register prototypes e2e`. Loads creds from MAS `.credentials.local`, runs on 189. Strict gates (`verify_sine_model_artifact_package.py`, `smoke_sine_model_artifact_inference.py`, `verify_sine_real_ai_e2e.py`) all enforced.

---

## Website release gate

Codex/Morgan can now run (website repo):

```powershell
node scripts/sine-release-gate.mjs --base=http://localhost:3010 --timeout=90000
```

Backend model/prototype/evidence blockers should be cleared. Website BFF still calls 189 directly (unchanged).
