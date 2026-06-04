# Acoustic Classifier Scope — May 27, 2026

**In scope:** SINE acoustic detectors, MINDEX Library acoustic blobs, NatureOS Library tab, `/sensing/sine/player`.

**Out of scope (disregard for this workstream):** Chemistry repos, DNA computing simulators, Cantera, DWSIM, MatChem-LLM, MQTT fixes, sandbox deploy, VM disk ops unless they block classifier tests.

---

## Product surfaces

| Surface | URL (dev) | Classifier trigger |
|---------|-----------|-------------------|
| MINDEX Library tab | `http://localhost:3010/natureos/mindex` → Acoustic | Server classification on blob (when wired) + client waveform heuristics as fallback |
| SINE player | `http://localhost:3010/sensing/sine/player` | `POST /api/mindex/sine/blobs/{id}/analyze` |

---

## Acoustic detectors (only these seven)

| ID | Role |
|----|------|
| `frequency_fft` | Dominant frequency peaks (FFT) |
| `activity_auditok` | Energy / activity segments (auditok) |
| `bird_microsoft` | Bird vs non-bird score (mel/heuristic; ONNX later) |
| `uav_rotor` | Rotor harmonic stacks |
| `nps_discovery_match` | NPS-style library profile match |
| `deep_signal_features` | Spectral embedding / pattern features |
| `visualisation_sonic` | Waveform + spectrogram JSON |

**Code:** `mindex_api/services/sine_acoustic/`  
**Registry:** `detector_registry.py`  
**Classifier entry:** `classifier.py` → `classify_acoustic_file()`  
**Library field mapping:** `event_views.py` → `frequency_detections`, `bird_detections`, etc.

---

## API (MINDEX VM 189)

```
POST /api/mindex/sine/blobs/{id}/analyze          # run + persist detection_event
GET  /api/mindex/sine/blobs/{id}/analysis        # flat events + visualisation
POST /api/mindex/library/blobs/{id}/classify      # same pipeline, Library-shaped JSON
GET  /api/mindex/library/blobs/{id}               # includes last classification when present
```

**Auth:** `X-Internal-Token` (website BFF forwards from `.env.local`).

---

## Frontend expectations (already built)

Library tab (`library-tab.tsx`) reads per blob:

- `frequency_detections`, `activity_segments`, `bird_detections`, `uav_detections`, `nps_detections`, `deep_signal_matches`
- `identification_summary` / `identification_status` / `analysis_engine`

SINE player uses flat `events[]` with `detector_id`, `start_sec`, `end_sec`.

Backend must **group** flat events into Library fields (see `event_views.py`).

---

## NLM relation (acoustic only)

- **NLM audio ingest:** `mindex_etl/jobs/ingest_nlm_audio_p0` — loads acoustic WAVs into `library.blob`.
- **NLM as classifier service:** not required for v1; SINE pipeline is the classifier until ONNX weights land on NAS under `models/acoustic/`.
- **First integration test:** pick one blob → `POST classify` → Library tab shows bird/UAV/frequency lanes without client-only heuristics.

---

## Next implementation steps

1. Deploy `event_views.py` + `classifier.py` + library `classify` route on 189.
2. From dev: `POST .../classify` on one acoustic blob; confirm grouped JSON.
3. Optional: Library tab button → call classify (website change — only when Morgan approves).
4. Upgrade `bird_microsoft` / `uav_rotor` from heuristics to ONNX when model files are on NAS.

---

**Status:** Acoustic classifier logic in repo; VM must have DB + `pip install -e '.[sine]'` for live runs.
