-- Unique keys for MOVER earth/ingest UPSERT (aircraft, vessels, satellites)
-- Date: Jun 18, 2026

CREATE UNIQUE INDEX IF NOT EXISTS uq_aircraft_source_source_id
    ON transport.aircraft (source, source_id)
    WHERE source_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vessels_source_source_id
    ON transport.vessels (source, source_id)
    WHERE source_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_satellites_source_source_id
    ON space.satellites (source, source_id)
    WHERE source_id IS NOT NULL;
