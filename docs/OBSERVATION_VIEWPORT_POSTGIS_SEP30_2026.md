# Observation viewport geometry and query qualification

The observations route accepts finite WGS84 rectangles `minLon,minLat,maxLon,maxLat`, with strict increasing bounds and no antimeridian wrapping. Stored locations are `geography(Point,4326)`. This change intersects their geometry cast with a planar SRID 4326 envelope so membership matches inclusive longitude/latitude coordinates. Spherical polygon edges define a different region and can be antipodal for valid full-world or polar rectangles. The native before run reproduced both wrong membership and PostGIS antipodal-edge errors.

This is a correctness repair with partial performance qualification. No production index, deployment, migration, global planner setting or schema alteration is included. Dateline wrapping remains rejected; clients may request two separate nonwrapping rectangles. Points represented at -180 and +180 retain their respective stored coordinate, as do longitudes at the poles. Empty geography points are excluded by ST_Intersects. Invalid metadata coordinate strings retain the existing SQL-cast behavior; this change does not repair that ingestion contract.

Only the viewport predicate changes. Taxon/kingdom/date filters, taxon join and metadata enrichment, descending observation-time ordering, pagination, optional exact counts and API-key dependency remain. Null-location metadata filtering and the existing response projection also remain: a metadata-only point can match the viewport while its response location is null. This compatibility behavior is tested rather than silently changed. Auth dependency wiring was preserved, not live-auth qualified.

## Native reproduction

Use a disposable PostgreSQL/PostGIS database literally named `map_fixture`, dedicated to this harness. The harness creates extension/schemas/tables and truncates its fixture tables; never point it at a database containing useful records. Dependencies are the existing application FastAPI, Pydantic and SQLAlchemy dependencies plus the psycopg driver. It loads the actual route, bbox parser and response DTO modules under an isolated package, substituting only dependency resolution and event scheduling; database SQL executes in native PostgreSQL/PostGIS via an async-shaped wrapper over synchronous SQLAlchemy. It does not start the server, invoke auth services or publish events.

```bash
export MAP_FIXTURE_DSN='postgresql+psycopg://FIXTURE_USER:FIXTURE_PASSWORD@FIXTURE_HOST/map_fixture'
export MAP_FIXTURE_RECEIPT=/tmp/map-fixture-tests.json
python -B tests/test_observation_viewport_postgis.py
# A separate destructive fixture rebuild followed by index/plan measurements:
export MAP_FIXTURE_RECEIPT=/tmp/map-fixture-benchmark.json
python -B tests/test_observation_viewport_postgis.py --benchmark
```

The DSN is explicit and checked for the exact database name. Isolation of the supplied server/role is the operator's responsibility; the name check is not a production safety boundary. The benchmark can consume a minute or more on a slow fixture host. Each measured SQL has a 5-second statement timeout; sample construction/index creation are limited by the external runner when used in CI.

Recorded environment: PostgreSQL 16.4, PostGIS 3.4.3, GEOS 3.9.0, Python 3.11 in an existing disposable container capped at 1 CPU/1 GiB, internal Docker network, no published ports or volumes. The other lane's retention databases were untouched. Synthetic correctness set: 377 geography points plus 4 null-location metadata rows. Twenty tests changed from 14 failures, 5 native errors and 1 pass to 20 passes. The receipt's `cases` field contains the 18 successful oracle-comparison checks; two other tests cover pagination/projection/count and invalid-input no-query behavior. Errors in the red run remain in its unittest log. The existing 79 bbox/Worldview tests also pass locally. This does not qualify concurrent browser performance or a live API response.

The inclusive oracle uses ST_X/ST_Y of actual stored points and numeric BETWEEN; fixtures include ordinary/zero-valued bounds, nextafter points just outside edges, poles, both dateline coordinates, width 180, width greater than 180 and whole-world rectangles. It does not treat index candidate overlap as proof of membership.

## Read-only production measurement

Source-generated full data/count statements were measured using EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON), BEGIN READ ONLY and default_transaction_read_only=on with a 5-second statement timeout. No observation rows were exported and no DDL/writes occurred. With the repaired predicate and existing indexes, San Diego data completed in 2.347 seconds wall time and whole-world data in 0.316 seconds. The (0,0,1,1) viewport data query and all three exact counts timed out at 5 seconds. This zero-valued viewport has positive area. These are individual bounded diagnostics, not p95 or capacity measurements; the historical geography query also timed out on the San Diego sample.

The current geography GiST index does not directly index the geometry cast. A separate expression-index candidate was tested only in the synthetic database:

```sql
CREATE INDEX CONCURRENTLY idx_observation_location_geometry
ON obs.observation USING gist ((location::geometry))
WHERE location IS NOT NULL;
```

Do not apply this proposal to production on the strength of sparse-viewport wins. A same-name existing index must be inspected, including definition/validity; IF NOT EXISTS alone is not verification. Concurrent index DDL must run outside a migration transaction and requires its own deployment/rollback review. The production migration runner was not changed or qualified here.

## Index experiment and remaining blocker

On 100,381 synthetic rows with only two synthetic taxa, the candidate index occupied 4,235,264 bytes and built in approximately 0.216 seconds. Two iterations per full SQL statement were measured in four phases: no expression index, index, index plus ANALYZE, and indexed force_custom_plan diagnostic. The 48 measurements are benchmark queries, not 48 additional test cases. The fixed synthetic row distribution and tiny taxon table cannot size production or predict concurrency. Results below are PostgreSQL execution milliseconds; they exclude connection/SSH wall overhead.

| Full statement | No expression index | Index, before ANALYZE | Index after ANALYZE |
|---|---:|---:|---:|
| San Diego data | 125–142 | 486–496 | 478–501 |
| San Diego count | 785–904 | 32–33 | 32–33 |
| (0,0,1,1) data | 190–193 | 2.6–3.2 | 2.9–3.1 |
| (0,0,1,1) count | 632–642 | 2.4–2.8 | 3.1–3.4 |
| Whole-world data | 103–104 | 2309–2447 | 2341–2415 |
| Whole-world count | 666–716 | 130–131 | 124–126 |

The indexed dense/world plans estimated about 10 matches while actually processing 20,014 / 100,380 matches, joining and sorting them before LIMIT. This observed underestimation accompanies the regression; its underlying PostGIS statistics/selectivity mechanism was not proven. ANALYZE did not resolve it. A fixture-only force_custom_plan phase likewise did not resolve it, and the recorded prepared-statement counters were empty. There is no evidence here for changing a global plan-cache setting.

Next acceptance gate: investigate a bounded query/index strategy on a representative production-independent snapshot, preserving exact coordinate membership, join/filter/count behavior and the default cheap-count policy. Require no material dense/world regression as well as sparse viewport improvement under the full query. Then separately review DDL, statistics, concurrent-build load, index size and rollback before applying any live change. Existing June-era production statistics are a separate maintenance consideration, not permission to ANALYZE production in this repair.

## Rollback and limits

Revert only the observations predicate patch to restore prior source behavior; that also restores its spherical-region errors. This code patch has no persistent schema effect. The optional fixture index can be removed only in its dedicated fixture database after retaining measurements. Any eventual live index rollback requires a separate approved DROP INDEX CONCURRENTLY and appropriate operational checks. Keep the geography index because other callers may use geography semantics.

No forecast, GPU, cloud, general geometry/polygon API, physical device, event pipeline, production write or production index was qualified. The source change can be reviewed independently of the unpromoted index. Full observation-map latency, exact-count latency, live route rollout and concurrent capacity remain open.
