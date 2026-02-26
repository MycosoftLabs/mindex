-- PostGIS Spatial Migration - February 17, 2026
-- Grounded Cognition: spatial points for geo-context storage

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS spatial_points (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id TEXT,
  lat DOUBLE PRECISION NOT NULL,
  lon DOUBLE PRECISION NOT NULL,
  h3_cell TEXT,
  ep_id TEXT,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_spatial_points_h3 ON spatial_points(h3_cell);
CREATE INDEX IF NOT EXISTS idx_spatial_points_session ON spatial_points(session_id);
CREATE INDEX IF NOT EXISTS idx_spatial_points_geo ON spatial_points USING GIST (ST_MakePoint(lon, lat));
