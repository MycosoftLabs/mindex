# obs.observation Schema Compatibility (Feb 11, 2026)

## Summary

The MINDEX API supports two schemas for `obs.observation`:

1. **PostGIS schema** (canonical): column `location` as `geography(Point, 4326)`. Used when PostGIS is installed.
2. **Lat/lng schema** (VM / no-PostGIS): columns `latitude` and `longitude` (numeric). Used on the MINDEX VM (192.168.0.189) where PostGIS is not installed.

## Code Changes

- **Stats router** (`mindex_api/routers/stats.py`): Tries `WHERE location IS NOT NULL` first; on exception (e.g. column missing), uses `WHERE latitude IS NOT NULL AND longitude IS NOT NULL`. `bio.genome` and `bio.taxon_trait` counts are wrapped in try/except so missing `bio` schema does not break stats.
- **Observations router** (`mindex_api/routers/observations.py`): Selects `latitude`, `longitude` and builds GeoJSON `{"type":"Point","coordinates":[lng,lat]}` in Python. Bbox filter uses `latitude BETWEEN :min_lat AND :max_lat AND longitude BETWEEN :min_lon AND :max_lon` instead of `ST_Intersects`.

## VM Deployment

After pulling these changes on the MINDEX VM, restart the API container so `/api/mindex/stats` and `/api/mindex/observations` work with the existing `obs.observation` table (lat/lng columns, no `location`).

## Reference

- Conversation summary: stats failed with `column "location" does not exist`; VM has lat/lng only.
- Migrations: `0001_init.sql` defines PostGIS `location`; VM was fixed with a no-PostGIS script creating lat/lng columns.
