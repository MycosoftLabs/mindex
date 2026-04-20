-- CREP live overlay tables — Apr 20, 2026
-- Used by /api/mindex/earth/map/bbox and bulk ingest from ETL / website registries.

CREATE SCHEMA IF NOT EXISTS crep;

CREATE TABLE IF NOT EXISTS crep.rail_live (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS rail_live_location_gix
    ON crep.rail_live USING GIST (location);
CREATE INDEX IF NOT EXISTS rail_live_observed_idx
    ON crep.rail_live (observed_at DESC);

CREATE TABLE IF NOT EXISTS crep.aircraft_live (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS aircraft_live_location_gix
    ON crep.aircraft_live USING GIST (location);
CREATE INDEX IF NOT EXISTS aircraft_live_observed_idx
    ON crep.aircraft_live (observed_at DESC);

CREATE TABLE IF NOT EXISTS crep.cctv_cameras (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS cctv_cameras_location_gix
    ON crep.cctv_cameras USING GIST (location);

CREATE TABLE IF NOT EXISTS crep.organizations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location GEOGRAPHY(POINT, 4326),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS organizations_location_gix
    ON crep.organizations USING GIST (location);
