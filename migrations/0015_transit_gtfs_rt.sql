BEGIN;

CREATE SCHEMA IF NOT EXISTS transit;

-- Static GTFS catalog (refreshed periodically by MAS transit_rt_collector)
CREATE TABLE IF NOT EXISTS transit.routes (
    agency text NOT NULL,
    route_id text NOT NULL,
    route_short_name text,
    route_long_name text,
    route_type integer,
    route_color text,
    props jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency, route_id)
);

CREATE TABLE IF NOT EXISTS transit.stops (
    agency text NOT NULL,
    stop_id text NOT NULL,
    stop_name text,
    geom geometry(Point, 4326),
    props jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency, stop_id)
);

CREATE INDEX IF NOT EXISTS idx_transit_stops_geom ON transit.stops USING GIST (geom);

CREATE TABLE IF NOT EXISTS transit.shapes (
    agency text NOT NULL,
    route_id text NOT NULL,
    shape_id text NOT NULL,
    geom geometry(LineString, 4326),
    route_color text,
    props jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agency, route_id, shape_id)
);

CREATE INDEX IF NOT EXISTS idx_transit_shapes_geom ON transit.shapes USING GIST (geom);

-- Live vehicle positions (10–30s refresh from GTFS-RT / Amtraker)
CREATE TABLE IF NOT EXISTS transit.vehicles (
    vehicle_uid text PRIMARY KEY,
    agency text NOT NULL,
    route_id text,
    trip_id text,
    geom geometry(Point, 4326) NOT NULL,
    bearing double precision,
    speed double precision,
    current_status text,
    stop_id text,
    next_stop_eta bigint,
    occupancy text,
    route_short_name text,
    route_color text,
    route_type integer,
    props jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transit_vehicles_geom ON transit.vehicles USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_transit_vehicles_agency ON transit.vehicles (agency);
CREATE INDEX IF NOT EXISTS idx_transit_vehicles_updated ON transit.vehicles (updated_at DESC);

COMMIT;
