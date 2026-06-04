# SINE Acoustic Backend — Full Stack (May 27, 2026)

**Status:** Implemented (MINDEX API + website BFF + player)  
**Product page:** [mycosoft.com/sensing/sine](https://mycosoft.com/sensing/sine)  
**Player URL (dev):** `http://localhost:3010/sensing/sine/player`

## What was built (from scratch)

| Layer | Path |
|-------|------|
| DB migration | `migrations/20260605_sine_acoustic_stack_may27_2026.sql` |
| Detectors registry | `mindex_api/services/sine_acoustic/detector_registry.py` |
| Analysis pipeline | `mindex_api/services/sine_acoustic/pipeline.py` + modules |
| MINDEX API | `/api/mindex/sine/*` — `mindex_api/routers/sine_acoustic.py` |
| Website BFF | `WEBSITE/website/app/api/mindex/sine/**` |
| UI player | `components/sensing/sine-acoustic-player.tsx` |
| Tests | `tests/test_sine_acoustic_pipeline.py` |
| Deploy | `_deploy_sine_acoustic_may27_2026.py` |

## Detectors (real upstream, server-side)

| ID | Upstream |
|----|----------|
| `frequency_fft` | [arduino-audio-tools frequency detection](https://github.com/pschatzmann/arduino-audio-tools/wiki/Simple-Frequency-Detection) |
| `activity_auditok` | [auditok](https://github.com/amsehili/auditok) |
| `bird_microsoft` | [microsoft/acoustic-bird-detection](https://github.com/microsoft/acoustic-bird-detection) |
| `uav_rotor` | [Acoustic-UAV-Identification](https://github.com/pcasabianca/Acoustic-UAV-Identification) |
| `nps_discovery_match` | [nationalparkservice/acoustic_discovery](https://github.com/nationalparkservice/acoustic_discovery) |
| `deep_signal_features` | [deep-signal](https://github.com/dimastatz/deep-signal) |
| `visualisation_sonic` | [Sonic Visualiser](https://www.sonicvisualiser.org/) layers |

Install on API container: `pip install -e '.[sine]'` (numpy, scipy, soundfile, auditok).

## API endpoints

```
GET  /api/mindex/sine/status
GET  /api/mindex/sine/detectors
GET  /api/mindex/sine/library/blobs
GET  /api/mindex/sine/library/blobs/{id}/stream
GET  /api/mindex/sine/blobs/{id}
POST /api/mindex/sine/blobs/{id}/analyze
GET  /api/mindex/sine/blobs/{id}/analysis
GET  /api/mindex/sine/blobs/{id}/visualisation
```

Requires `X-Internal-Token` (same as other MINDEX internal routes).

## Website BFF (proxies 189)

```
GET  /api/mindex/sine/status
GET  /api/mindex/sine/detectors
GET  /api/mindex/sine/library/blobs
GET  /api/mindex/sine/library/blobs/{id}/stream
POST /api/mindex/sine/blobs/{id}/analyze
GET  /api/mindex/sine/blobs/{id}/analysis
GET  /api/mindex/sine/blobs/{id}/visualisation
```

## Deploy (VM 189)

```powershell
cd MINDEX\mindex
python _deploy_sine_acoustic_may27_2026.py
```

Or manually:

1. Apply migration on `mindex-postgres`
2. `docker compose exec api pip install -e '.[sine]'`
3. `docker compose up -d api --force-recreate`

## Verify

```powershell
$h = @{ "X-Internal-Token" = $env:MINDEX_INTERNAL_TOKEN }
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/sine/status -Headers $h
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/sine/detectors -Headers $h
```

Browser: open `http://localhost:3010/sensing/sine/player`, select a library clip, **Run full SINE analysis**.

## Codex frontend

Sonic Visualiser polish can extend `sine-acoustic-player.tsx` (layers, zoom, annotations). Backend already returns `waveform` + `spectrogram` JSON for canvas rendering.

## Honest limits

- Bird/UAV detectors use **spectral heuristics** on the VM CPU; swap in published ONNX weights when models are staged on NAS.
- `deep-signal` full Spark MediaRDD is not embedded; single-file **spectral embedding** mode is used for pattern matching until Spark cluster is provisioned.
- Library must have ingested WAVs (`library.blob`); run `ingest_nlm_audio_p0` if empty.
