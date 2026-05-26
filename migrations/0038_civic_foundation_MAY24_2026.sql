-- Civic/Government viewport intelligence foundation (May 24, 2026)
-- Canonical civic schema + cache/lineage substrate for MINDEX-first viewport intel.

BEGIN;

CREATE SCHEMA IF NOT EXISTS civic;

COMMENT ON SCHEMA civic IS
  'Canonical civic/government intelligence entities and viewport cache for Earth Simulator right-panel intelligence.';

CREATE TABLE IF NOT EXISTS civic.jurisdictions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_key       text NOT NULL UNIQUE,
  country             text,
  country_code        text,
  state               text,
  county              text,
  city                text,
  open_civic_division_id text,
  display_name        text,
  centroid            geography(Point, 4326),
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_civic_jurisdictions_division
  ON civic.jurisdictions (open_civic_division_id);
CREATE INDEX IF NOT EXISTS idx_civic_jurisdictions_centroid
  ON civic.jurisdictions USING GIST (centroid);

CREATE TABLE IF NOT EXISTS civic.officials (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_key       text NOT NULL UNIQUE,
  jurisdiction_id     uuid REFERENCES civic.jurisdictions(id) ON DELETE SET NULL,
  name                text NOT NULL,
  office              text,
  level               text,
  party               text,
  image_url           text,
  source              text,
  confidence_score    double precision DEFAULT 0.8,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_civic_officials_jurisdiction
  ON civic.officials (jurisdiction_id);

CREATE TABLE IF NOT EXISTS civic.official_contacts (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  official_id         uuid NOT NULL REFERENCES civic.officials(id) ON DELETE CASCADE,
  contact_type        text NOT NULL,
  contact_value       text NOT NULL,
  source              text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (official_id, contact_type, contact_value)
);

CREATE TABLE IF NOT EXISTS civic.elections (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_key       text NOT NULL UNIQUE,
  jurisdiction_id     uuid REFERENCES civic.jurisdictions(id) ON DELETE SET NULL,
  name                text NOT NULL,
  election_day        date,
  source_url          text,
  source              text,
  confidence_score    double precision DEFAULT 0.8,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_civic_elections_jurisdiction
  ON civic.elections (jurisdiction_id);
CREATE INDEX IF NOT EXISTS idx_civic_elections_day
  ON civic.elections (election_day DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS civic.facilities (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_key       text NOT NULL UNIQUE,
  jurisdiction_id     uuid REFERENCES civic.jurisdictions(id) ON DELETE SET NULL,
  name                text NOT NULL,
  facility_type       text,
  position            geography(Point, 4326),
  agency              text,
  source              text,
  confidence_score    double precision DEFAULT 0.75,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_civic_facilities_jurisdiction
  ON civic.facilities (jurisdiction_id);
CREATE INDEX IF NOT EXISTS idx_civic_facilities_position
  ON civic.facilities USING GIST (position);

CREATE TABLE IF NOT EXISTS civic.facility_images (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id         uuid NOT NULL REFERENCES civic.facilities(id) ON DELETE CASCADE,
  image_url           text NOT NULL,
  license             text,
  attribution         text,
  source              text,
  usage_rights        text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (facility_id, image_url)
);

CREATE TABLE IF NOT EXISTS civic.source_lineage (
  id                  bigserial PRIMARY KEY,
  entity_type         text NOT NULL,
  entity_key          text NOT NULL,
  source_name         text NOT NULL,
  source_record_id    text,
  fetched_at          timestamptz NOT NULL DEFAULT now(),
  confidence_score    double precision DEFAULT 0.8,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_civic_source_lineage_entity
  ON civic.source_lineage (entity_type, entity_key, fetched_at DESC);

CREATE TABLE IF NOT EXISTS civic.viewport_cache (
  id                  bigserial PRIMARY KEY,
  cache_key           text NOT NULL UNIQUE,
  north               double precision NOT NULL,
  south               double precision NOT NULL,
  east                double precision NOT NULL,
  west                double precision NOT NULL,
  zoom                double precision NOT NULL,
  lod                 text,
  place_name          text,
  jurisdiction_key    text,
  payload             jsonb NOT NULL,
  generated_at        timestamptz NOT NULL DEFAULT now(),
  expires_at          timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_civic_viewport_cache_expires
  ON civic.viewport_cache (expires_at);
CREATE INDEX IF NOT EXISTS idx_civic_viewport_cache_bbox
  ON civic.viewport_cache (north, south, east, west, zoom);

COMMIT;
