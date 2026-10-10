# M-Wave measured-input preparation

The existing M-Wave routes exposed conventional USGS detections as if a bioelectric network was monitoring. This change separates detected earthquakes from unqualified research, preserves null counts on failed feeds, and removes inferred zero sensor/all-clear states. No trained earthquake predictor, calibrated hardware adapter, retained evaluation or persistence adapter is bound.

The [M-Wave paper](https://storage.prod.researchhub.com/uploads/papers/users/100041/845e8316-910c-421d-906c-ff2131e92882/The%20M%20Wave-%20Harnessing%20Mycelium%20Networks%20for%20Earthquake%20Prediction.pdf) describes a research hypothesis and proposed synchronized bioelectric/seismic pipeline. Its page-five demonstration explicitly uses dummy prediction logic; those constants are not implemented as a model. Pages six and seven propose preprocessing, feature extraction, candidate models and evaluation. No downloadable trained predictor was identified in the paper or inspected MINDEX/MAS source. Deterministic earthquake prediction remains unsupported; [USGS distinguishes prediction](https://www.usgs.gov/faqs/can-you-predict-earthquakes) from [early warning after an earthquake begins](https://www.usgs.gov/programs/earthquake-hazards/science/earthquake-early-warning-fine-tuning-best-alerts).

## API contract

The existing router registration retains `/api/mindex` and `internal_deps` in `mindex_api/main.py`. New routes inherit that internal authorization; no public sensor submission or hardware-control path is added.

- `GET /api/mindex/mwave`: bounded parallel USGS hour/day reads, source generation times, measured event identifiers/locations/links. Each source can fail independently. `sensor_count` and `prediction_confidence` remain null. Detected-event notices are tagged `is_prediction:false`.
- `GET /api/mindex/mwave/readiness`: `mwave.research.v1`, unbound model and unavailable replay, false prediction/public alerts. Existing FCI tables are queried read-only for a candidate snapshot: 128 rows, ten minutes, SQL deadline two seconds and whole query deadline three seconds. Candidate readings do not verify device identity, calibration, source hashes or scientific skill; qualified `current_sensor_count` stays null.
- `GET /api/mindex/mwave/input-schema`: actual Pydantic JSON schema for recorded bioelectric windows. Explicit source kind, immutable source reference/hash, calibration reference, sample time/rate/units/values, coordinates, clock uncertainty, quality and confounder-record references are required.
- `POST /api/mindex/mwave/readiness/evaluate`: metadata and sample schema assessment only. Maximum one MiB, five-second input-read deadline, 64 windows/16,384 samples. Nonfinite values, bad units/coordinates, naive dates, simulated measured inputs, stale current inputs and samples beyond the analysis cutoff are rejected. An accepted request remains unverified hardware, unbound model, unpersisted and prediction-disabled. Archive/laboratory inputs require explicit research replay; this does not create a retained replay or evaluated model.

The contract provides preparation stages, not successful training. A later research adapter must verify source hashes against retained raw signals, calibration/device records, synchronized confounders and event labels, then attach a versioned model artifact and chronological held-out evaluation. Both positive and negative periods and false alarms must be retained. No adapter should learn from samples after its analysis cutoff. Public alerts remain disabled throughout this preparation.

## Qualification

Run `python -m unittest discover -s tests -p test_mwave_research_readiness.py -v` with the repository's Python requirements. Ten checks exercise actual FastAPI routes and Pydantic schemas, read-only store budgets, partial/offline catalog semantics and sanitized errors. Database tests use explicit fixtures, not PostgreSQL. Router tests load the concrete router rather than all unrelated integrations; full application startup/authentication remains unqualified.

On October 9, direct execution of the new router against actual USGS feeds returned eight hourly and 264 daily events in 0.484 seconds. Source generation timestamps were retained. Counts are a dated observation, not a persistent expectation. Private receipt: `CODE/.codex-artifacts/earth-live-review-oct09/mwave-router-usgs-live.json`.

The Website preview still points to an older MINDEX deployment: measured catalog reads work, but the new readiness/schema paths return 503. This branch requires the reviewed MINDEX release before those bindings become available. No production deployment, GPU inference, hardware validation, scientific evaluation or alert qualification was performed.
