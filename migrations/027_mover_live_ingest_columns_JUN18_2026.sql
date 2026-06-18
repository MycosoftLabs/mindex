-- MOVER live ingest columns — align transport/space with earth/ingest + bbox API
-- Date: Jun 18, 2026

CREATE EXTENSION IF NOT EXISTS postgis;

ALTER TABLE transport.aircraft
    ADD COLUMN IF NOT EXISTS source_id VARCHAR(100);

ALTER TABLE transport.vessels
    ADD COLUMN IF NOT EXISTS source_id VARCHAR(100);

ALTER TABLE space.satellites
    ADD COLUMN IF NOT EXISTS source_id VARCHAR(100);

ALTER TABLE space.satellites
    ADD COLUMN IF NOT EXISTS location GEOGRAPHY(POINT, 4326);

ALTER TABLE space.satellites
    ADD COLUMN IF NOT EXISTS altitude_km DOUBLE PRECISION;

ALTER TABLE space.satellites
    ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_sat_geo ON space.satellites USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_sat_obs ON space.satellites (observed_at DESC);
