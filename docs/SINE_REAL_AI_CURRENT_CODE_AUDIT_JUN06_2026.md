# SINE Real AI Current Code Audit

Date: June 6, 2026

Prepared by: Codex for Cursor

Repo:

`D:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex`

Website contract source:

`D:\Users\admin2\Desktop\MYCOSOFT\CODE\WEBSITE\website\docs\codex-handoffs\SINE_REAL_BACKEND_CURSOR_FULL_HANDOFF_JUN06_2026.md`

## One Sentence Directive

Morgan QA-tested SINE and confirmed that `Run SINE analysis` is not doing real AI classification yet. The current backend has useful detector, visualization, model-registry, request-contract, runtime-inspection, and evidence-persistence scaffolding, but no deployed loaded artifact has proven learned PyTorch/TorchScript/ONNX/transformer inference on VM 189. Cursor must preserve the honesty/plumbing patches and then build real model-backed acoustic classification.

## Original Failure Morgan Found

A real ESC-50 blob could stream and the backend could return HTTP 200, but the analysis response was not backed by a real model.

Original bad response shape:

```json
{
  "status": "complete",
  "model_status": null,
  "identification_summary": {
    "top_label": "bird_likely",
    "engine": "bird_microsoft",
    "model": "mindex_sine_v1",
    "status": "classified"
  },
  "model_outputs": [],
  "fusion_evidence": [],
  "sound_transcripts": [],
  "deep_signal_matches": [
    {
      "label": "spectral_embedding",
      "metadata": {
        "embedding_dim": 20,
        "upstream": "dimastatz/deep-signal"
      }
    }
  ]
}
```

This was a backend bug. Detector evidence is allowed. Semantic identity is not allowed unless it is tied to real model output, prototype/fingerprint evidence, fusion evidence, or evidence-linked transcripts.

## Current June 6 Local Source State

Codex rechecked the current local MINDEX source again after the Website model-detail and backend handoff updates. Treat this as the current local working-tree state unless a newer MINDEX commit changes these files.

Current source evidence:

- `mindex_api/services/sine_acoustic/event_views.py` no longer promotes detector-only rows into `identification_summary`.
- `event_views.py` no longer hard-codes `model: "mindex_sine_v1"`.
- `tests/test_acoustic_event_views.py` now asserts that detector labels such as `bird_likely` do **not** become a top semantic label without model/prototype/fusion/transcript proof.
- `mindex_api/services/sine_acoustic/model_runtime.py` inspects registry/runtime/artifact readiness but explicitly does not classify audio.
- `mindex_api/services/sine_acoustic/persisted_evidence.py` can read saved `sine.model_output`, `sine.prototype_match`, `sine.fusion_evidence`, and `sine.sound_transcript` rows when Cursor's future inference runner writes them.
- `mindex_api/services/sine_acoustic/request_contract.py` preserves the Website evidence contract.
- `mindex_api/routers/sine_acoustic.py` has `/sine/models`, `/sine/models/{model_id}`, and `/sine/prototypes`.
- `migrations/20260606_sine_model_registry_jun06_2026.sql` creates model/prototype registry tables.
- `migrations/20260606_sine_analysis_evidence_jun06_2026.sql` creates model/prototype/fusion/transcript evidence tables.
- `pipeline.py` still calls only `frequency_fft`, `activity_auditok`, `bird_microsoft`, `uav_rotor`, `nps_discovery_match`, `deep_signal_features`, and `visualisation_sonic`.
- No deployed VM path has proven PyTorch, TorchScript, ONNX Runtime, transformer embeddings, prototype search, evidence fusion, or transcript generation on real UUID-backed audio. The local analyze route now has a runner seam for this once a loaded checksum-verified artifact exists. The Library classify route remains a detector/latest-evidence view.

Current conclusion:

The honesty/plumbing patch is present locally. The next backend patch must be the real inference runner, not another semantic frontend workaround:

```text
model_status = "model_unavailable" or "detector_only"
identification_summary = null/omitted
model_outputs = []
prototype_matches = []
deep_signal_matches = []
fusion_evidence = []
sound_transcripts = []
```

Only after a real registered model artifact is checksum-verified and loaded may Cursor return semantic identity.

## Codex Local Honesty Patch - Not Yet VM-Deployed

Codex applied the first honesty patch locally in the MINDEX repo after the source recheck.

Changed files:

```text
mindex_api/services/sine_acoustic/event_views.py
mindex_api/routers/sine_acoustic.py
mindex_api/routers/library.py
tests/test_acoustic_event_views.py
```

Behavior now expected from local MINDEX code:

- Detector labels such as `bird_likely`, `uav_rotor_likely`, and `spectral_embedding` remain visible only as detector evidence.
- `event_views.py` no longer promotes detector buckets into `identification_summary`.
- `event_views.py` no longer hard-codes `model: "mindex_sine_v1"`.
- Deep-signal detector rows are returned as `deep_signal_detections`, not semantic `deep_signal_matches`.
- Detector-only classify/analyze payloads return:
  - `model_status: "model_unavailable"`
  - `identification_status: "detector_only"`
  - `identification_summary: null`
  - `model_outputs: []`
  - `deep_signal_matches: []`
  - `prototype_matches: []`
  - `fusion_evidence: []`
  - `sound_transcripts: []`
  - fallback diagnostics set to false
- The `library` and `sine_acoustic` routers now forward those honesty fields to clients instead of dropping them.

Local verification:

```powershell
python -m pytest tests
```

Result:

```text
115 passed, 3 skipped
```

There was a pytest cache cleanup permission warning at process exit, but the test command returned success.

Important deployment note:

This local patch has not been deployed to MINDEX VM 189 in this Codex pass. Until Cursor or Morgan deploys these MINDEX changes, `localhost:3010` may still show the old VM behavior (`bird_likely`, missing `model_status`, and `mindex_sine_v1`).

## Codex Local Oscilloscope Visualisation Patch - Not Yet VM-Deployed

Codex also patched the local MINDEX visualisation path so the backend can produce oscilloscope-grade waveform/spectrogram data from decoded audio bytes.

Changed files:

```text
mindex_api/services/sine_acoustic/visualisation.py
mindex_api/routers/sine_acoustic.py
tests/test_sine_acoustic_pipeline.py
```

Behavior now expected from local MINDEX code:

- `GET /api/mindex/sine/blobs/{id}/visualisation` accepts and honors:
  - `start_sec`
  - `end_sec`
  - `max_waveform_points` / `waveform_points`
  - `max_time_frames` / `spec_time_bins`
  - `max_frequency_bins` / `spec_freq_bins`
  - `fft_size` / `n_fft`
  - `hop_length`
  - `window_function`
  - `db_floor`
  - `db_ceiling`
  - `include_peaks`
  - `quality`
  - `ignore_saved_visualisation`
- `ignore_saved_visualisation=true` bypasses stale low-resolution saved visualisation rows.
- Ordinary short clips can return 8,192 waveform points and a 256 x 1,024 spectrogram.
- The response includes:
  - `visualisation_status`
  - `channels`
  - `fft_size`
  - `hop_length`
  - `window_function`
  - frequency bounds
  - dB bounds
  - clamp/downsample metadata
  - peak rows
  - `dsp_backend`
- If SciPy is installed, the backend uses `scipy.signal.spectrogram`.
- If SciPy is not installed, the backend falls back to a real NumPy STFT via `numpy.rfft` instead of failing or generating synthetic data.

Local direct smoke output:

```text
status=ready
dsp_backend=numpy.rfft
waveform=8192
frequencies=256
times=1024
power_rows=256
power_cols=1024
peaks=48
fft_size=2048
hop_length=128
```

Local verification:

```powershell
python -m pytest tests
```

Result after the visualisation patch:

```text
116 passed, 3 skipped
```

This visualisation patch is also local only until deployed to VM 189.

## Codex Local Model/Prototype Registry Endpoint Patch - Not Yet VM-Deployed

Codex added honest SINE registry surfaces locally so the Website does not have to treat missing registry routes as ambiguous backend behavior.

Changed files:

```text
mindex_api/routers/sine_acoustic.py
migrations/20260606_sine_model_registry_jun06_2026.sql
tests/test_api_contract_openapi.py
tests/test_sine_registry_contract.py
```

New local endpoints:

```text
GET /api/mindex/sine/models
GET /api/mindex/sine/models/{model_id}
GET /api/mindex/sine/prototypes
```

Behavior:

- If `sine.model_artifact` does not exist, `/sine/models` returns:
  - `ok: false`
  - `status: "model_registry_unavailable"`
  - `model_status: "model_unavailable"`
  - empty `models`, `registered_models`, and `loaded_models`
- If `sine.prototype` does not exist, `/sine/prototypes` returns:
  - `ok: false`
  - `status: "prototype_catalog_unavailable"`
  - `model_status: "model_unavailable"`
  - empty `prototypes` and `prototype_catalog`
- `/sine/status` now includes:
  - `model_status`
  - `model_ready`
  - `registered_models`
  - `loaded_models`
- The migration creates the planned `sine.model_artifact` and `sine.prototype` tables, but does not insert fake rows.

Local verification:

```powershell
python -m pytest tests
```

Result after the registry endpoint patch:

```text
118 passed, 3 skipped
```

This patch is local only until deployed to VM 189 and the migration is applied.

## Codex Local Request-Contract Patch - Not Yet VM-Deployed

Codex added a local request/evidence contract path so the Website's SINE evidence requirements are preserved by MINDEX instead of ignored.

Changed files:

```text
mindex_api/services/sine_acoustic/request_contract.py
mindex_api/services/sine_acoustic/event_views.py
mindex_api/services/sine_acoustic/classifier.py
mindex_api/routers/sine_acoustic.py
mindex_api/routers/library.py
tests/test_acoustic_event_views.py
tests/test_sine_request_contract.py
```

Behavior now expected from local MINDEX code:

- `POST /api/mindex/sine/blobs/{id}/analyze` reads the JSON body sent by the Website BFF.
- `POST /api/mindex/library/blobs/{id}/classify` reads the same evidence contract body.
- The response includes top-level `request_contract`.
- `diagnostics.request_contract` mirrors the same contract for auditability.
- The saved `library.analysis_run.summary` includes:
  - `model_status`
  - `identification_status`
  - `request_contract`
- `GET /api/mindex/sine/blobs/{id}/analysis` rebuilds the same honest classification payload from the saved summary.
- The contract records:
  - `evidence_contract`
  - `sine_request`
  - `requested_outputs`
  - `target_domains`
  - `class_families`
  - `sound_targets`
  - `visualisation_quality`
  - `requires_registered_model`
  - `allows_detector_only`

This still does not implement PyTorch, TorchScript, ONNX Runtime, transformer embeddings, prototype search, or sound transcripts. It only makes the future model work auditable and keeps detector-only analysis honest.

Earlier focused local verification before the stricter E2E/package-verifier additions:

```powershell
python -m pytest tests\test_acoustic_event_views.py tests\test_sine_request_contract.py tests\test_sine_registry_contract.py tests\test_sine_acoustic_pipeline.py tests\test_api_contract_openapi.py
```

Result:

```text
10 passed, 1 skipped
```

Full local verification after the request-contract patch:

```powershell
python -m pytest tests
```

Result:

```text
119 passed, 3 skipped
```

Pytest still emits an existing Windows `.pytest_cache` / temp cleanup permission warning at process exit, but the command returned success.

## Codex Local Runtime-Inspection Patch - Not Yet VM-Deployed

Codex added an honest runtime-readiness layer, not a model runner.

Changed files:

```text
mindex_api/services/sine_acoustic/model_runtime.py
mindex_api/services/sine_acoustic/event_views.py
mindex_api/services/sine_acoustic/classifier.py
mindex_api/routers/sine_acoustic.py
mindex_api/routers/library.py
tests/test_acoustic_event_views.py
tests/test_sine_model_runtime.py
```

Behavior now expected from local MINDEX code:

- `/api/mindex/sine/status` now reports model readiness through `inspect_sine_model_runtime(...)` instead of trusting a DB row marked loaded.
- Analyze/classify responses include `model_context`.
- Diagnostics include:
  - `model_status`
  - `model_ready`
  - `model_registry_ready`
  - `prototype_catalog_ready`
  - `runtime_backends`
  - `runtime_supported`
  - `inference_ready`
  - `blocking_reasons`
- `runtime_backend_status()` checks whether optional Torch and ONNX Runtime dependencies are installed without importing heavy modules.
- `artifact_path_from_uri()` accepts only local/NAS paths or `file://` URIs. It intentionally rejects request-time HTTP/S3 model artifacts.
- `sha256_file()` is available for Cursor's future artifact checksum verification.

Current limitation:

- No PyTorch, TorchScript, ONNX Runtime, transformer, CRNN, prototype, fusion, or transcript inference is implemented yet.
- Even when a registry row exists, this local patch only reports `model_runtime_available` at most. It must not set `model_ready` or emit `model_outputs[]` until real inference runs on decoded audio and returns artifact/checksum-backed results.

Focused local verification after the runtime-inspection patch:

```powershell
python -m pytest tests\test_acoustic_event_views.py tests\test_sine_request_contract.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_acoustic_pipeline.py tests\test_api_contract_openapi.py
```

Result:

```text
12 passed, 1 skipped
```

Full local verification after the runtime-inspection patch:

```powershell
python -m pytest tests
```

Result:

```text
121 passed, 3 skipped
```

The same Windows pytest temp/cache cleanup permission warning appeared after success.

Later evidence-persistence verification supersedes this count. See the next section for the current June 6 local test result.

## Codex Local Evidence-Persistence Patch - Not Yet VM-Deployed

Codex added the database and response contract for real model-backed evidence. This is still not a model runner, but it gives Cursor the durable tables that real inference must write.

Changed files:

```text
migrations/20260606_sine_analysis_evidence_jun06_2026.sql
mindex_api/services/sine_acoustic/persisted_evidence.py
mindex_api/services/sine_acoustic/event_views.py
mindex_api/routers/sine_acoustic.py
mindex_api/routers/library.py
tests/test_acoustic_event_views.py
tests/test_sine_evidence_migration_contract.py
```

New planned tables:

```text
sine.model_output
sine.prototype_match
sine.fusion_evidence
sine.sound_transcript
```

Behavior now expected from local MINDEX code:

- `GET /api/mindex/sine/blobs/{id}/analysis` reads persisted model/prototype/fusion/transcript rows if the tables exist.
- Library blob detail uses the same persisted evidence reader through `_latest_classification(...)`.
- Before the migration exists or before data is written, these arrays remain empty and the backend stays detector-only.
- `build_library_classification_payload(...)` accepts:
  - `model_outputs`
  - `prototype_matches`
  - `fusion_evidence`
  - `sound_transcripts`
- `identification_summary` is created only from one of those proven rows.
- Detector rows such as `bird_likely`, `uav_rotor_likely`, or `spectral_embedding` still cannot create an identification summary.
- `sound_transcripts[]` only influence identity when they include model, fusion, or prototype evidence IDs.
- If persisted model/prototype/fusion/transcript evidence exists, stale saved `model_unavailable` context no longer overrides the real evidence status or leaves stale blocking reasons in diagnostics.
- The migration grants `SELECT, INSERT, UPDATE, DELETE` to `mindex` and does not insert any fake rows.

Focused local verification after the evidence-persistence patch:

```powershell
python -m pytest tests\test_acoustic_event_views.py tests\test_sine_evidence_migration_contract.py tests\test_sine_request_contract.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_acoustic_pipeline.py tests\test_api_contract_openapi.py
```

Result:

```text
15 passed, 1 skipped
```

Full local verification after the evidence-persistence patch:

```powershell
python -m pytest tests
```

Result:

```text
124 passed, 3 skipped
```

Compile and whitespace verification after the evidence-persistence patch:

```powershell
python -m compileall mindex_api\routers\sine_acoustic.py mindex_api\routers\library.py mindex_api\services\sine_acoustic
git diff --check -- mindex_api/routers/sine_acoustic.py mindex_api/routers/library.py mindex_api/services/sine_acoustic/event_views.py mindex_api/services/sine_acoustic/model_runtime.py mindex_api/services/sine_acoustic/persisted_evidence.py mindex_api/services/sine_acoustic/request_contract.py mindex_api/services/sine_acoustic/visualisation.py migrations/20260606_sine_analysis_evidence_jun06_2026.sql migrations/20260606_sine_model_registry_jun06_2026.sql tests/test_acoustic_event_views.py tests/test_sine_evidence_migration_contract.py tests/test_sine_request_contract.py tests/test_sine_model_runtime.py tests/test_sine_registry_contract.py tests/test_sine_acoustic_pipeline.py tests/test_api_contract_openapi.py docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

Result:

```text
compileall passed
git diff --check returned no whitespace errors
```

Git emitted line-ending normalization warnings for some dirty files. Those warnings are not test failures.

## Codex Continuation Verification - June 6 Focused SINE Scaffold

Codex reran the focused local SINE scaffold verification after cleaning the handoff and current-code audit text.

Command:

```powershell
python -m pytest tests\test_acoustic_event_views.py tests\test_sine_evidence_migration_contract.py tests\test_sine_request_contract.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_acoustic_pipeline.py tests\test_api_contract_openapi.py
```

Result:

```text
15 passed, 1 skipped
```

Warnings:

- Starlette `python_multipart` pending deprecation.
- Existing Pydantic v2 class-based config deprecation warnings in unrelated schema files.
- Existing duplicate OpenAPI operation ID warning for `library_catalog`.
- Existing Windows `.pytest_cache` write permission warning after the tests completed successfully.

Additional commands:

```powershell
python -m compileall mindex_api\routers\sine_acoustic.py mindex_api\routers\library.py mindex_api\services\sine_acoustic
git diff --check -- mindex_api/routers/sine_acoustic.py mindex_api/routers/library.py mindex_api/services/sine_acoustic/classifier.py mindex_api/services/sine_acoustic/event_views.py mindex_api/services/sine_acoustic/model_runtime.py mindex_api/services/sine_acoustic/persisted_evidence.py mindex_api/services/sine_acoustic/request_contract.py mindex_api/services/sine_acoustic/visualisation.py migrations/20260606_sine_analysis_evidence_jun06_2026.sql migrations/20260606_sine_model_registry_jun06_2026.sql tests/test_acoustic_event_views.py tests/test_sine_evidence_migration_contract.py tests/test_sine_request_contract.py tests/test_sine_model_runtime.py tests/test_sine_registry_contract.py tests/test_sine_acoustic_pipeline.py tests/test_api_contract_openapi.py docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

Result:

- `compileall` passed.
- `git diff --check` passed with line-ending normalization warnings only.

Interpretation:

- The local honesty, request-contract, model-runtime, registry, evidence-persistence, and visualisation scaffold is green.
- This does not prove real SINE. The active backend still needs a real learned inference runner that writes `sine.model_output`, `sine.prototype_match`, `sine.fusion_evidence`, and evidence-linked `sine.sound_transcript`.

### `mindex_api/services/sine_acoustic/event_views.py`

Current local state:

- `build_identification_summary()` now returns a semantic identity only from model outputs, prototype matches, linked fusion evidence, or evidence-linked transcripts.
- Detector-only rows such as `bird_likely`, `uav_rotor_likely`, NPS heuristic rows, and `spectral_embedding` no longer become top-level identity.
- Detector-only payloads return `identification_summary: null`, `identification_status: "detector_only"`, and `model_status: "model_unavailable"` unless real evidence is supplied.
- `deep_signal_features` rows are kept as `deep_signal_detections`; `deep_signal_matches` remains empty until real prototype evidence exists.

Remaining work:

- Cursor must keep this honesty behavior while adding real model/prototype/fusion/transcript evidence.
- Do not reintroduce detector promotion while wiring the new inference runner.

### `mindex_api/services/sine_acoustic/pipeline.py`

Current issue:

- `run_full_analysis()` only dispatches detector modules.
- It does not call PyTorch, TorchScript, ONNX Runtime, transformer embeddings, prototype search, or evidence fusion.
- It does not accept window bounds or evidence-contract requirements.

Fix:

- Split detector execution from a real `analysis_runner.py`.
- Add real audio window decoding.
- Add missing-model behavior.
- Add real model inference only after a registered artifact is checksum-validated and loaded.

### `mindex_api/services/sine_acoustic/deep_signal.py`

Current issue:

- Returns a 20-value mean spectral profile as `spectral_embedding`.
- Metadata references `dimastatz/deep-signal`, but no real neural model or prototype search ran.
- The row lacks `prototype_id`, `embedding_id`, `model_id`, `embedding_sha256`, `vector_sha256`, score/distance proof, and source provenance.

Current local guard:

- It is no longer returned as semantic `deep_signal_matches`.
- Treat it as non-semantic DSP/debug evidence if retained.

Remaining work:

- Only return `deep_signal_matches` for real prototype/fingerprint matches with model/vector/source proof.

### `mindex_api/services/sine_acoustic/bird.py` and `uav.py`

Current issue:

- Heuristics emit `bird_likely`, `uav_rotor_likely`, or related labels.
- These can help detector lanes, but they are not final acoustic meaning.

Fix:

- Keep as detector evidence only.
- Add metadata such as `evidence_kind: "detector"` and `semantic_role: "non_semantic_signal_evidence"`.
- Do not let these rows write top-level identity or transcript prose.

### `mindex_api/services/sine_acoustic/visualisation.py`

Earlier issue:

- Defaults are 800 waveform points, 128 time bins, and 64 frequency bins.
- The Website asks for oscilloscope-grade data and currently receives tiny visualisation.
- Missing metadata: FFT size, hop length, window function, channel count, frequency bounds, dB bounds, clamp state, visualisation status, and peaks.

Current local state:

- Honor query params for `start_sec`, `end_sec`, waveform points, spectrogram rows/columns, FFT size, hop length, window function, and peak extraction.
- Return real decoded arrays from NAS audio bytes.
- For ordinary short clips, support 8,192 waveform points and 1,024 x 256 spectrogram cells.

Remaining work:

- Cursor must deploy and verify this path on VM 189 and large MBARI/Psathyrella windows.

### `mindex_api/routers/sine_acoustic.py`

Earlier issue:

- `/status` reports detectors and acoustic blobs, not model readiness.
- `POST /blobs/{id}/analyze` accepts only `detectors`.
- It ignores the Website evidence contract.
- It stores only `library.analysis_run` and `library.detection_event`.
- `GET /blobs/{id}/analysis` returns the latest run only and has no window/job targeting.
- `GET /blobs/{id}/visualisation` ignores quality params and can return stale low-resolution data.

Current local state:

- Accept evidence contract JSON/query fields:
  - `require_real_audio`
  - `require_model_evidence`
  - `semantic_fallback=false`
  - `llm_fallback=false`
  - `prototype_matching=true`
  - `sound_transcripts=evidence_backed_only`
  - target domains and class families
  - requested outputs
  - `start_sec`, `end_sec`, `window_sec`, `window_index`
- Add real model/prototype routes:
  - `GET /api/mindex/sine/models`
  - `GET /api/mindex/sine/models/{model_id}`
  - `GET /api/mindex/sine/prototypes`
- Return model registry truth from `/status`.
- Add queued/windowed handling for large MBARI, hydrophone, and Psathyrella files.

Remaining work:

- Cursor must implement queued/windowed handling for large files and live streams.
- Cursor must wire the router to a real inference runner that writes `sine.model_output`, `sine.prototype_match`, `sine.fusion_evidence`, and `sine.sound_transcript`.

## Tests That Now Protect The Honesty Gate

Current local tests no longer encode the old false-label behavior:

- `tests/test_acoustic_event_views.py`
  - `test_build_identification_summary_does_not_promote_detector_labels()` asserts that `bird_likely` does not become a top semantic label.
  - `test_build_library_classification_payload_keys()` asserts detector-only model-unavailable payload shape.
  - model-output and transcript tests assert semantic identity only when proof-backed rows exist.
- `tests/test_sine_model_runtime.py`, `tests/test_sine_registry_contract.py`, `tests/test_sine_request_contract.py`, and `tests/test_sine_evidence_migration_contract.py` cover scaffolding for future real inference.

Required next tests:

- `tests/test_sine_real_model_inference.py`
- `tests/test_sine_prototype_matching.py`
- `tests/test_sine_fusion_evidence.py`
- `tests/test_sine_sound_transcripts.py`
- `tests/test_sine_windowed_long_file_analysis.py`
- `tests/test_sine_human_review_training_queue.py`

Missing-model test must assert:

```text
model_status == "model_unavailable"
identification_summary is absent or null
model_outputs == []
deep_signal_matches == []
prototype_matches == []
fusion_evidence == []
sound_transcripts == []
detector evidence may still exist
semantic_fallback_used == false
llm_fallback_used == false
filename_fallback_used == false
metadata_fallback_used == false
```

## Website Request Contract Cursor Must Honor

The Website now sends a canonical `sine_request` and `evidence_contract` from:

- `/api/mindex/sine/blobs/{id}/analyze`
- `/api/natureos/mindex/library/classify?id={id}`
- the shared SINE player direct analyze action

The request includes:

- `target_domains`: `water`, `air`, `ground`
- broad `class_families`
- explicit `sound_targets`
- requested model/prototype/fusion/transcript outputs
- model/prototype/vector/transcript evidence requirements
- 30-second default windows for long audio
- oscilloscope visualisation quality requirements

Cursor must read and persist `sine_request.sound_targets` on analysis runs. Do not collapse the target list to birds and UAVs. The current frontend target set includes whales, dolphins, fish choruses, birds, mammals, amphibians, insects, soil bioacoustics, hydrophone unknown biologics, UAVs, helicopters, aircraft, boat/submerged propellers, vessel/submarine hums, sonar, machinery, actuators, explosions, blasts, impacts, lightning/thunder, rain/wind, earthquake/seismic, surface/underground ground motion, water pressure impulses, unknown/OOD, and human-contested labels.

If a requested target has no model/prototype coverage yet, return an honest `unknown`, `out_of_domain`, or `model_unavailable` state instead of a semantic guess.

## Required Real Backend After Honesty Patch

Build the real classifier in this order:

1. Real audio decoder for NAS-backed `library.blob` UUIDs.
2. Windowed decode with `start_sec`, `end_sec`, and 30-second defaults for long files.
3. Oscilloscope-grade visualisation with waveform, spectrogram, FFT/STFT metadata, channel metadata, dB/frequency bounds, clamp state, and peak rows.
4. `sine` schema for model artifacts, label maps, analysis runs, detector events, model outputs, prototypes, prototype matches, fusion evidence, sound transcripts, human review queue, and training examples.
5. Model registry and `/api/mindex/sine/models`.
6. One real P0 ESC-50/environmental model using PyTorch, TorchScript, or ONNX Runtime.
7. Real model inference that persists `sine.model_output`.
8. Embeddings and prototype/fingerprint retrieval with stable prototype IDs and vector checksums.
9. Evidence fusion from detector events, model outputs, prototype matches, and human review.
10. Sound transcripts only when rows link to evidence IDs.
11. Human correction loop where Morgan can mark a model label wrong, save the human label, and queue contested rows for training review.

## Hard Rules

- No mock labels.
- No Gemini or LLM classifier.
- No filename/path/source-metadata classifier.
- No generated/synthetic audio rows.
- No semantic label without model/prototype/fusion evidence.
- No transcript prose without evidence IDs.
- No model readiness claims without artifact/checksum/runtime proof.

## Acceptance

Cursor cannot call this complete until:

- missing-model analysis honestly returns `model_unavailable`.
- one short ESC-50 clip returns real model output with model artifact, runtime, checksum, label map, window bounds, top labels, and latency.
- one hydrophone/MBARI/Psathyrella 30-second window returns a bounded result or queued job.
- one negative/OOD case produces no fake semantic label.
- one human correction persists beside a model prediction and appears in the training-review queue.

## Quick Cursor Execution Checklist

Run this order:

1. Review and preserve the local honesty/registry/request-contract/runtime/evidence patches.
2. Confirm detector-only rows cannot produce `identification_summary`, `classification_status=classified`, or `model=mindex_sine_v1`.
3. Confirm `bird.py`, `uav.py`, and `deep_signal.py` rows remain detector/DSP evidence, not semantic identity.
4. Deploy/apply the registry and evidence migrations if they are not already on VM 189.
5. Verify `/api/mindex/sine/status`, `/api/mindex/sine/models`, `/api/mindex/sine/models/{model_id}`, and `/api/mindex/sine/prototypes`.
6. Verify real NAS audio decode and high-definition visualisation on a short ESC-50 blob and one bounded hydrophone/MBARI/Psathyrella window.
7. Add P0 ESC-50 PyTorch/TorchScript/ONNX model artifact, label map, checksums, and registry row.
8. Implement the inference runner and persist real outputs to `sine.model_output`.
9. Add prototype/fingerprint matching with IDs and vector checksums.
10. Add fusion evidence and evidence-linked transcripts.
11. Add human correction/training-review queue integration.
12. Verify through `http://localhost:3010/sensing/sine` that the Website evidence gate clears for one real model-backed clip and stays blocked for detector-only output.
13. Verify one visualisation returns real high-resolution decoded arrays.
14. Verify one human correction persists and queues review.
15. Verify Website `localhost:3010` clears `MINDEX contract failed` only for a real model-backed short clip.

## Codex Continuation - Stricter Evidence Readiness Patch

Codex tightened the local `event_views.py` evidence gate after rechecking the current MINDEX source against the Website SINE traceability matrix.

Changed files:

```text
mindex_api/services/sine_acoustic/event_views.py
tests/test_acoustic_event_views.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What changed:

- `identification_summary` now rejects model output rows unless they include:
  - semantic label,
  - model identity,
  - artifact or label-map checksum,
  - confidence or OOD metric.
- `identification_summary` now rejects prototype/deep-signal rows unless they include:
  - semantic label,
  - stable `prototype_id`,
  - vector or prototype checksum,
  - score or distance.
- Raw unproven `model_outputs`, `prototype_matches`, `fusion_evidence`, and `sound_transcripts` remain visible in the payload for frontend audit/debugging, but they no longer flip `model_status` to `model_ready`.
- Detector-only or unproven-evidence payloads now keep `model_status` from the actual model runtime context and add `unproven_model_or_prototype_evidence` to `diagnostics.blocking_reasons`.

Focused local verification:

```powershell
python -m pytest tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
16 passed, 1 skipped
```

This is still not a real classifier. It is a stronger honesty gate that prevents shallow or partially persisted backend rows from looking like real acoustic intelligence before Cursor implements the actual PyTorch/TorchScript/ONNX inference, prototype search, evidence fusion, and evidence-linked transcript path.

## Codex Continuation - Real DSP Feature/Windowing Layer

Codex added the first reusable backend feature extraction layer for real model inference. This is still semantic-free; it does not identify whales, birds, UAVs, lightning, or any other class. It prepares deterministic tensors that the future PyTorch/TorchScript/ONNX runtime can consume.

Changed files:

```text
mindex_api/services/sine_acoustic/features.py
tests/test_sine_feature_extraction.py
```

What the new module provides:

- `iter_audio_windows(...)` for bounded analysis windows over long NAS files.
- `fixed_length_samples(...)` for deterministic model-window padding/cropping.
- `stft_power(...)` using NumPy FFT.
- `mel_filterbank(...)` without external dependencies.
- `log_mel_spectrogram(...)` for deterministic log-mel model input features.
- `extract_sine_feature_tensor(...)` returning a `[1, 1, n_mels, frames]` float32 tensor plus metadata.
- `feature_sha256(...)` so model outputs and prototype embeddings can record stable feature provenance.

Why this matters:

- It implements the real feature-input layer requested by the external audio code audit: log-mel/MFCC-style DSP, fixed clip windows, long-file splitting, and checksumable feature tensors.
- It gives Cursor a concrete target to plug into `inference_runtime.py` rather than inventing another fake classifier path.
- It keeps semantics out of the feature layer. The feature tensor is evidence, not an identity.

Focused local verification:

```powershell
python -m pytest tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
20 passed, 1 skipped
```

Cursor's next backend implementation should consume `extract_sine_feature_tensor(...)` in the real inference runner, persist `feature_sha256` alongside `sine.model_output` and `sine.prototype_match`, and keep `identification_summary` blocked until model/prototype/fusion/transcript evidence links are present.

## Codex Continuation - TorchScript/ONNX Runtime Interface

Codex added the first real inference runtime seam. This module still does not register or train a model, and this machine does not currently have `torch` or `onnxruntime` installed. Its job is to make the future model path concrete and honest.

Changed files:

```text
mindex_api/services/sine_acoustic/inference_runtime.py
tests/test_sine_inference_runtime.py
```

What the new module provides:

- `run_registered_model_inference(...)` for registered local model artifacts.
- Local-only artifact URI handling; remote `http`, `https`, and `s3` model artifacts are rejected by the underlying model-runtime path rules.
- Artifact SHA-256 verification before execution.
- Local label-map loading from JSON lists, `{labels: [...]}`, `{classes: [...]}`, or integer-keyed dictionaries.
- Label-map SHA-256 verification.
- Sample-rate mismatch rejection so decode/resample remains explicit.
- Runtime dependency checks for TorchScript/PyTorch and ONNX Runtime.
- Real tensor generation through `extract_sine_feature_tensor(...)`.
- Stable softmax/top-k mapping from model scores to labels.
- Provenance-rich output fields: artifact checksum, label-map checksum, feature checksum, tensor shape, runtime, latency, model identity, and label scores.

Important local runtime status:

```text
torch=False
onnxruntime=False
numpy=True
```

Because Torch and ONNX Runtime are absent locally, the tests mock only the execution call while still verifying artifact checksums, label-map checksums, feature tensor metadata, output postprocessing, and missing-runtime behavior. This is a contract test, not a fake production classifier.

Focused local verification:

```powershell
python -m pytest tests\test_sine_inference_runtime.py tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
24 passed, 1 skipped
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor's next backend step should install/verify `torch` or `onnxruntime` in the MINDEX API/worker image, register a real model artifact under `/mnt/nas/mindex/models/acoustic/{model_id}`, run `run_registered_model_inference(...)` on a UUID-backed acoustic blob, persist the returned output into `sine.model_output`, and only then let the Website evidence gate clear.

## Codex Continuation - Model Output Persistence Runner

Codex added the first local bridge from the registered-model runtime seam into the persisted SINE evidence tables. This still does not train, register, or load a real model artifact; it makes the active `/sine/blobs/{id}/analyze` path capable of writing real model evidence once Cursor provides a checksum-verified TorchScript/ONNX artifact and installs the runtime dependencies.

Changed files:

```text
mindex_api/services/sine_acoustic/analysis_runner.py
mindex_api/routers/sine_acoustic.py
tests/test_sine_analysis_runner.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What the new module provides:

- `select_loaded_acoustic_models(...)` selects registered acoustic models from `sine.model_artifact` only when `loaded` or `model_ready`.
- `build_model_output_insert_params(...)` converts a proven `run_registered_model_inference(...)` result into `sine.model_output` insert parameters.
- `persist_model_output(...)` writes model outputs with model ID, label, confidence, top-k scores, artifact checksum, label-map checksum, runtime timing, feature checksum, tensor shape, sample rate, and window bounds.
- `run_and_persist_loaded_model_outputs(...)` decodes the real NAS-backed blob audio, runs the loaded model runtime, persists successful outputs, and returns honest blocking reasons when the model table, loaded model, runtime, artifact, label map, checksum, or inference result is missing.
- `POST /api/mindex/sine/blobs/{id}/analyze` now calls this runner after detector events are written and before the analysis run is marked complete.

Current behavior:

- With no loaded model or no Torch/ONNX runtime, the runner writes no semantic evidence and reports `model_outputs_unavailable`.
- With a successful real inference result, the runner writes `sine.model_output` rows in the same analysis run so `persisted_evidence.py` and `event_views.py` can expose them to the Website evidence gate.
- The router still does not create prototype matches, fusion evidence, or sound transcripts. Cursor must implement those next.

Focused local verification:

```powershell
python -m pytest tests\test_sine_analysis_runner.py tests\test_sine_inference_runtime.py tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
28 passed, 1 skipped
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor's next backend step is now sharper: deploy/preserve this runner, install or verify Torch/ONNX Runtime in the MINDEX container, register a real local artifact and label map under `/mnt/nas/mindex/models/acoustic/{model_id}`, run a short ESC-50 UUID-backed blob through `/api/mindex/sine/blobs/{id}/analyze`, verify that `sine.model_output` contains a real row, and then build prototype matching, evidence fusion, and evidence-linked sound transcripts around that persisted output.

## Codex Continuation - Model-Backed Fusion And Transcript Writer

Codex added the next local evidence step after `sine.model_output`: a conservative writer for `sine.fusion_evidence` and `sine.sound_transcript`. This still does not perform prototype matching, vector search, model training, or real semantic inference. It only creates downstream evidence rows after a proven model output has already been persisted.

Changed files:

```text
mindex_api/services/sine_acoustic/evidence_builder.py
mindex_api/services/sine_acoustic/analysis_runner.py
tests/test_sine_evidence_builder.py
tests/test_sine_analysis_runner.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What the new module provides:

- `build_fusion_evidence_insert_params(...)` creates a `model_output_identity` fusion row only when the model output has:
  - persisted output ID,
  - model ID,
  - semantic label,
  - artifact SHA-256,
  - label-map SHA-256,
  - confidence or OOD metric.
- `build_sound_transcript_insert_params(...)` creates a chronological transcript row only when it can link to both a model-output ID and a fusion-evidence ID.
- `persist_evidence_for_model_output(...)` writes the fusion and transcript rows after `persist_model_output(...)` succeeds.
- `run_and_persist_loaded_model_outputs(...)` now returns `fusion_evidence[]` and `sound_transcripts[]` alongside persisted model outputs.

Current behavior:

- Detector-only analysis still writes no semantic transcript or fusion evidence.
- Unproven model output rows still produce no fusion or transcript.
- Prototype matching remains intentionally incomplete until Cursor implements real embedding/vector/prototype search with stable prototype IDs and vector/prototype checksums.

Focused local verification:

```powershell
python -m pytest tests\test_sine_evidence_builder.py tests\test_sine_analysis_runner.py tests\test_sine_inference_runtime.py tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
33 passed, 1 skipped
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor's next required backend step remains: install or verify Torch/ONNX Runtime, register a real model artifact and label map, run a real UUID-backed acoustic blob so the new runner writes real `sine.model_output`, `sine.fusion_evidence`, and `sine.sound_transcript` rows, then implement prototype matching and OOD/open-set behavior.

## Codex Continuation - Prototype Vector Search Seam

Codex added a local prototype search seam that performs deterministic cosine similarity over real numeric vectors. This still does not create prototype rows, train embeddings, or run a real model. It activates only when a model runtime returns an embedding vector and `sine.prototype` contains stored prototype vectors in `metadata`.

Changed files:

```text
mindex_api/services/sine_acoustic/prototype_search.py
mindex_api/services/sine_acoustic/analysis_runner.py
tests/test_sine_prototype_search.py
tests/test_sine_analysis_runner.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What the new module provides:

- `extract_query_embedding(...)` reads embedding vectors from explicit runtime result fields such as `embedding`, `embedding_vector`, `feature_embedding`, `vector`, or `embedding_output.vector`.
- `cosine_similarity(...)` and `vector_sha256(...)` provide deterministic vector scoring and proof.
- `select_candidate_prototypes(...)` reads acoustic prototypes from `sine.prototype`.
- Prototype vectors are read from prototype `metadata.vector`, `metadata.embedding`, `metadata.centroid`, or `metadata.prototype_vector`.
- `run_and_persist_prototype_matches(...)` writes `sine.prototype_match` rows only when:
  - query embedding exists,
  - prototype table exists,
  - prototype vector exists,
  - cosine score is above threshold,
  - model-output ID and prototype ID are present.
- `analysis_runner.py` now calls the prototype seam after a proven model output is persisted and before fusion/transcript rows are built.

Current behavior:

- If the model returns only labels and no embedding vector, prototype matching is skipped with no semantic match.
- If `sine.prototype` has no rows or no usable vectors, no `deep_signal_matches` are emitted.
- `deep_signal_matches` remains backed by real `sine.prototype_match` rows, not the old `deep_signal_features` detector row.

Focused local verification:

```powershell
python -m pytest tests\test_sine_prototype_search.py tests\test_sine_evidence_builder.py tests\test_sine_analysis_runner.py tests\test_sine_inference_runtime.py tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
39 passed, 1 skipped
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor's next required backend step is to make the registered model return a real embedding vector, populate `sine.prototype` from human-tagged/library examples with stored vectors and checksums, and verify a real `sine.prototype_match` row on VM 189.

## Codex Continuation - Analysis Visualisation Quality Contract

Codex connected the Website visualisation-quality contract into the active `Run SINE analysis` path. The direct `/visualisation` route already accepts high-definition oscilloscope parameters locally; this patch makes `classify_acoustic_file(...)` and `run_full_analysis(...)` honor the same density/settings when `visualisation_sonic` runs as part of analyze/classify.

Changed files:

```text
mindex_api/services/sine_acoustic/classifier.py
mindex_api/services/sine_acoustic/pipeline.py
tests/test_sine_classifier_visualisation_contract.py
tests/test_sine_acoustic_pipeline.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What changed:

- `run_full_analysis(...)` accepts `visualisation_options`.
- `visualisation_sonic` forwards those options to `build_visualisation_layers(...)`.
- `classify_acoustic_file(...)` maps `request_contract.visualisation_quality` fields into safe visualisation options.
- Supported contract fields include:
  - `max_waveform_points` / `waveform_points`
  - `max_time_frames` / `spec_time_bins`
  - `max_frequency_bins` / `spec_freq_bins`
  - `fft_size` / `n_fft`
  - `hop_length`
  - `window_function`
  - `db_floor`
  - `db_ceiling`
  - `include_peaks`
  - `quality`
  - `start_sec`
  - `end_sec`
- Standard detector-only runs still use standard visualisation defaults unless the request contract explicitly asks for oscilloscope quality.

Focused local verification:

```powershell
python -m pytest tests\test_sine_classifier_visualisation_contract.py tests\test_sine_prototype_search.py tests\test_sine_evidence_builder.py tests\test_sine_analysis_runner.py tests\test_sine_inference_runtime.py tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
42 passed, 1 skipped
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor must deploy this local path to VM 189 and verify both:

1. `GET /api/mindex/sine/blobs/{uuid}/visualisation?...quality=oscilloscope` returns high-definition arrays.
2. `POST /api/mindex/sine/blobs/{uuid}/analyze` with the Website evidence contract returns high-definition `classification.visualisation` / top-level `visualisation`.

## Codex Continuation - Multi-Output Model Runtime Embeddings

Codex updated the local inference runtime so a real TorchScript/ONNX model can return both class logits and an embedding vector. This is required before prototype matching can ever become real.

Changed files:

```text
mindex_api/services/sine_acoustic/inference_runtime.py
mindex_api/services/sine_acoustic/prototype_search.py
tests/test_sine_inference_runtime.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What changed:

- `_run_torchscript(...)` no longer discards tuple/list/dict outputs.
- `_run_onnx(...)` returns all ONNX outputs instead of only the first output.
- Runtime output parsing now supports:
  - direct logits arrays,
  - `[logits, embedding]`,
  - `(logits, embedding)`,
  - `{ "logits": ..., "embedding": ... }`,
  - `{ "scores": ..., "embedding_vector": ... }`,
  - related explicit score/vector key shapes.
- Successful inference results now include:
  - `embedding`
  - `embedding_sha256`
  - `embedding_dim`
- `prototype_search.vector_from_value(...)` now accepts NumPy arrays as embedding vectors.

Focused local verification:

```powershell
python -m pytest tests\test_sine_inference_runtime.py tests\test_sine_prototype_search.py tests\test_sine_classifier_visualisation_contract.py tests\test_sine_evidence_builder.py tests\test_sine_analysis_runner.py tests\test_sine_feature_extraction.py tests\test_acoustic_event_views.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
44 passed, 1 skipped
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor's P0 model export should therefore return a two-output model: classification logits plus embedding vector. That lets `sine.model_output` persist label evidence, `prototype_search.py` persist nearest-prototype evidence, and `evidence_builder.py` link the result into fusion/transcript rows.

## Codex Continuation - Open-Set / OOD Runtime Metrics

Codex added the first local open-set guard around model outputs. This does not replace real calibration; it prevents the future runtime seam from treating weak logits as reliable acoustic identity.

Changed files:

```text
mindex_api/services/sine_acoustic/inference_runtime.py
mindex_api/services/sine_acoustic/event_views.py
tests/test_sine_inference_runtime.py
tests/test_acoustic_event_views.py
docs/SINE_REAL_AI_CURRENT_CODE_AUDIT_JUN06_2026.md
```

What changed:

- `run_registered_model_inference(...)` now returns:
  - `confidence_margin`
  - `entropy`
  - `normalized_entropy`
  - `ood_score`
  - `ood_status`
  - `ood_threshold`
  - `min_confidence`
- The OOD score combines low top probability, low top-2 margin, and high normalized entropy.
- Runtime feature params may provide `min_confidence` and `ood_threshold`.
- Current statuses are:
  - `in_domain_candidate`
  - `low_confidence`
  - `out_of_domain_candidate`
- `event_views.py` refuses to promote model outputs with `low_confidence`, `out_of_domain`, or `out_of_domain_candidate` into `identification_summary`.

Focused local verification:

```powershell
python -m pytest tests\test_sine_library_classify_contract.py tests\test_sine_model_artifact_package_verifier.py tests\test_sine_esc50_training_artifact_script.py tests\test_sine_inference_runtime.py tests\test_acoustic_event_views.py tests\test_sine_prototype_search.py tests\test_sine_classifier_visualisation_contract.py tests\test_sine_evidence_builder.py tests\test_sine_analysis_runner.py tests\test_sine_feature_extraction.py tests\test_sine_model_runtime.py tests\test_sine_registry_contract.py tests\test_sine_request_contract.py tests\test_sine_evidence_migration_contract.py tests\test_sine_acoustic_pipeline.py
```

Result:

```text
70 passed, 1 skipped, 5 warnings
```

There was a Windows pytest cache/temp cleanup permission warning after the successful run. The command returned success.

Cursor still needs to calibrate OOD thresholds with real validation data, not defaults. The acceptance target is: weak/unknown clips must become `unknown`, `low_confidence`, `out_of_domain_candidate`, or queued review; they must not become confident semantic identities.

## Codex Continuation - ESC-50 P0 Artifact Builder

Codex added the first local artifact-builder scaffold for the P0 learned model:

```text
scripts/train_sine_esc50_p0.py
scripts/verify_sine_model_artifact_package.py
scripts/smoke_sine_model_artifact_inference.py
scripts/build_sine_prototype_catalog.py
scripts/verify_sine_real_ai_e2e.py
tests/test_sine_esc50_training_artifact_script.py
tests/test_sine_model_artifact_package_verifier.py
tests/test_sine_model_artifact_runtime_smoke_script.py
tests/test_sine_prototype_catalog_builder_script.py
tests/test_sine_real_ai_e2e_verifier.py
tests/test_sine_library_classify_contract.py
```

This is still not a deployed classifier. It does not insert fake registry rows, does not mark a model loaded, and does not write analysis evidence. Its job is to turn real ESC-50 WAV files plus real ESC-50 labels into a checksum-backed artifact package that the existing `sine.model_artifact` registry and `inference_runtime.py` can consume.

The script trains a small TorchScript CNN that returns two outputs:

- classification logits
- embedding vector

The package output contains:

```text
model.torchscript.pt
labels.json
metrics.json
confusion_matrix.json
training_manifest.json
model_registry_row.json
register_model_artifact.sql
```

Example:

```powershell
python scripts\train_sine_esc50_p0.py `
  --audio-root /mnt/nas/mindex/Library/acoustic/esc50 `
  --metadata-csv /mnt/nas/mindex/Library/acoustic/esc50/meta/esc50.csv `
  --output-root /mnt/nas/mindex/models/acoustic `
  --epochs 12 `
  --batch-size 32
```

If the metadata CSV is missing, copy the real ESC-50 `meta/esc50.csv` into NAS or use real manifest labels. Do not use filename target ids as production semantic labels unless they are mapped back to official ESC-50 metadata.

Before applying the generated registration SQL, verify the package:

```powershell
python scripts\verify_sine_model_artifact_package.py `
  --package-root /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1 `
  --expected-model-id sine-esc50-cnn-p0-v1 `
  --write-report /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/verification_report.json
```

The verifier does not load the model, mark it ready, or write to Postgres. It fails on missing required package files, checksum mismatches, label/metric/confusion/manifest disagreement, malformed registry SQL, missing target/class/feature metadata, or any package that claims `loaded=true` before runtime proof. It now also rejects duplicate labels, non-square or non-integer confusion matrices, missing or inconsistent train/validation counts, and incomplete inference feature metadata; required `feature_params` include `n_fft`, `hop_length`, `n_mels`, `max_frames`, and `window_sec`.

After package verification, use the runtime smoke script:

```powershell
python scripts\smoke_sine_model_artifact_inference.py `
  --package-root /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1 `
  --wav-path /mnt/nas/mindex/Library/acoustic/esc50/<known-esc50-clip>.wav `
  --expected-model-id sine-esc50-cnn-p0-v1 `
  --write-report /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/runtime_smoke_report.json `
  --write-loaded-sql /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/mark_model_loaded.sql
```

The runtime smoke script verifies the package, decodes a real WAV, calls `run_registered_model_inference(...)`, omits raw embedding vectors from the JSON report, refuses OOD/low-confidence smoke output by default, and writes guarded `mark_model_loaded.sql` only after inference succeeds. It does not apply SQL itself.

After runtime smoke, build prototype vectors:

```powershell
python scripts\build_sine_prototype_catalog.py `
  --package-root /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1 `
  --audio-root /mnt/nas/mindex/Library/acoustic/esc50 `
  --metadata-csv /mnt/nas/mindex/Library/acoustic/esc50/meta/esc50.csv `
  --expected-model-id sine-esc50-cnn-p0-v1 `
  --min-examples-per-label 5 `
  --write-json /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/prototypes.json `
  --write-sql /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/register_prototypes.sql
```

The prototype builder verifies the artifact package, runs the model on real labeled WAVs, averages valid embeddings per label, writes checksummed prototype rows for `sine.prototype`, and refuses OOD/low-confidence embeddings by default. It does not apply SQL itself.

Final live API proof:

```powershell
python scripts\verify_sine_real_ai_e2e.py `
  --api-base http://192.168.0.189:8000 `
  --query esc `
  --write-report /mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/e2e_real_ai_report.json
```

The E2E verifier calls live MINDEX status/models/prototypes, runs or reads SINE analysis for a UUID-backed acoustic blob, and fails unless the response contains loaded model proof, checksum-backed prototype catalog rows, provenance-backed model outputs, scored prototype matches, linked fusion evidence, and evidence-linked transcripts. It also cross-links the evidence chain: the analysis model output must match a loaded registry row by `model_id`, `artifact_sha256`, and `label_map_sha256`, and each counted prototype match must point at a registered prototype catalog ID.

Focused local verification:

```powershell
python -m pytest tests\test_sine_real_ai_e2e_verifier.py tests\test_sine_prototype_catalog_builder_script.py tests\test_sine_model_artifact_runtime_smoke_script.py tests\test_sine_library_classify_contract.py tests\test_sine_model_artifact_package_verifier.py tests\test_sine_esc50_training_artifact_script.py tests\test_sine_acoustic_pipeline.py tests\test_sine_classifier_visualisation_contract.py
python scripts\train_sine_esc50_p0.py --help
python scripts\verify_sine_model_artifact_package.py --help
python scripts\smoke_sine_model_artifact_inference.py --help
python scripts\build_sine_prototype_catalog.py --help
python scripts\verify_sine_real_ai_e2e.py --help
python -m py_compile scripts\train_sine_esc50_p0.py scripts\verify_sine_model_artifact_package.py scripts\smoke_sine_model_artifact_inference.py scripts\build_sine_prototype_catalog.py scripts\verify_sine_real_ai_e2e.py
```

Result:

```text
72 passed, 1 skipped, 5 warnings
```

The expanded pytest run passed the SINE real-AI E2E verifier, prototype catalog builder, runtime smoke guard, Library classify contract, stricter artifact package verifier, artifact-builder, inference runtime, prototype search, evidence, request-contract, registry, and visualisation tests. The latest E2E verifier tests also prove catalog rows are checked differently from scored per-run prototype matches, and that analysis evidence must cross-link to the loaded model registry and prototype catalog. The artifact verifier tests also prove malformed confusion matrices and mismatched validation totals fail before registration. The pytest run emitted a Windows cache/temp cleanup permission warning after success. Cursor's next step is to run this artifact builder on VM 189 or a suitable training machine with Torch installed, run the package verifier, inspect metrics, apply `register_model_artifact.sql`, run the runtime smoke, apply `mark_model_loaded.sql` only if the smoke passes, build/apply prototype catalog SQL, then run the E2E verifier and prove real `sine.model_output`, `sine.prototype_match`, `sine.fusion_evidence`, and `sine.sound_transcript` rows exist.
