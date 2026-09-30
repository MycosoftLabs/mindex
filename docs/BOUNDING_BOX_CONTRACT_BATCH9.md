# Bounding-box validation and Worldview observation delegation

This repair addresses B5-R06 (invalid bounds accepted) and B8-M01 (FastAPI defaults leaking through a direct Worldview call). It changes input validation and delegation only. It adds no geography SQL, antimeridian conversion, authentication policy or database schema.

## Existing contract, now enforced consistently

`bbox` is an optional comma-separated string `minLon,minLat,maxLon,maxLat`. Observation routes and fungal-overlay cells/samples/land ranking now share `mindex_api/utils/bbox.py`, behind their existing `_parse_bbox` wrappers.

- All four values must be finite numbers. NaN, either infinity and values overflowing to infinity return HTTP 400.
- Longitudes must lie within `[-180, 180]`; latitudes within `[-90, 90]`. Endpoints are inclusive, and zero coordinates remain valid.
- Existing strict positive-area order remains: `minLon < maxLon` and `minLat < maxLat`. Degenerate, reversed and wrapped boxes are rejected. A tuple such as `170,-10,-170,10` is not interpreted as a dateline crossing.
- Omitted or empty-string bounds retain the existing unfiltered behavior. Whitespace around numbers and finite scientific notation remain accepted. Wrong length and malformed numbers retain the existing error details/status.
- Successful parsing returns exactly `min_lon`, `min_lat`, `max_lon`, `max_lat`. Response DTOs, overlay metadata, SQL bindings and coordinate order remain unchanged.

Default mounted paths include `/api/mindex/observations` and `/api/mindex/fungal-overlays/{cells,samples,deployment/land}`. Prefixes remain configurable. The Worldview observation route defaults to `/api/worldview/v1/species/observations`; it passes the bbox unchanged. Its direct call now explicitly supplies `kingdom=None` and `include_total=False`, preserving the public parameter set and avoiding both `Query.strip()` failure and an accidental exact-count query. Internal callers can still explicitly request an exact count.

## Validation evidence

The focused fixtures import selected real parser, route and Pydantic DTO modules. Application/config/auth/deep-agent/governance dependencies are isolated; SQL execution and result rows are fake. The composed Worldview call executes its actual delegation and the actual observation list body, checking typed output, geographic bindings, absence of an implicit count, propagation of invalid input before SQL/events, and propagation of database errors without a success envelope. Overlay tests cover each dependent entry point and preserve its distinct response shape. This is not a live app/auth/payment/governance/PostGIS test.

Pre-fix: **39 failed / 53 passed** across the final 92 cases. Post-fix: **92 passed**, comprising 79 new cases and 13 unchanged prior bulk-transaction regressions. The latter use the existing temporary SQLite/SQLAlchemy fixture; they do not validate PostGIS SQL. The run reported 85 warnings from existing pytest configuration, Starlette multipart usage, Pydantic class-based config (repeated imports), and overlay `datetime.utcnow` calls. Those unrelated warnings are unchanged by this repair.

An equivalent local invocation, with test dependencies installed, is:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest --noconftest -p no:cacheprovider -o addopts= -q tests/test_batch9_bbox_worldview_contract.py tests/test_batch6_bulk_transaction_contract.py
```

The audit additionally used a runner blocking selected socket/process audit events and credential basenames `.env`, `.env.*`, `agent.env`, `.credentials.local`; this is not a general OS sandbox. The inspected tests loaded no production credentials and made no actual service or database connection.

## Limits and rollback

Accepting a boundary tuple at the parser does not prove correct geography behavior for global/polar envelopes. Existing observation geography SQL and overlay numeric filtering remain untouched. Supporting wrapped boxes, choosing a longitude normalization policy or validating global PostGIS results requires separate work. Other earth/transit parsers and website query forwarding are outside this patch. Governance/auth behavior is unchanged and was stubbed in the composition fixtures.

Rollback reverses this incremental change, including the helper, fixtures and this note, while retaining all earlier bulk/ingestion/search repairs. It reintroduces the unsafe numeric acceptance and Worldview direct-default failure. No database migration or data rollback is needed. This authoring lane performed no original checkout edits, stage/commit/push, live request or deployment; release integration is separately coordinated.
