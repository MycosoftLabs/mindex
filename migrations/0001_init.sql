BEGIN;

-- Required extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Schemas
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS bio;
CREATE SCHEMA IF NOT EXISTS obs;
CREATE SCHEMA IF NOT EXISTS telemetry;
CREATE SCHEMA IF NOT EXISTS ip;
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS app;

-- Core schema
CREATE TABLE IF NOT EXISTS core.taxon (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id uuid REFERENCES core.taxon (id) ON DELETE SET NULL,
    canonical_name text NOT NULL,
    rank text NOT NULL,
    common_name text,
    authority text,
    description text,
    source text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_taxon_canonical_name ON core.taxon (canonical_name);
CREATE INDEX IF NOT EXISTS idx_taxon_rank ON core.taxon (rank);

CREATE TABLE IF NOT EXISTS core.taxon_external_id (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
    source text NOT NULL,
    external_id text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_taxon_external_source_id
    ON core.taxon_external_id (source, external_id);

CREATE TABLE IF NOT EXISTS core.taxon_synonym (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
    synonym text NOT NULL,
    source text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Bio schema
CREATE TABLE IF NOT EXISTS bio.taxon_trait (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
    trait_name text NOT NULL,
    value_text text,
    value_numeric double precision,
    value_unit text,
    source text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_taxon_trait_name ON bio.taxon_trait (trait_name);
CREATE INDEX IF NOT EXISTS idx_taxon_trait_taxon ON bio.taxon_trait (taxon_id);

CREATE TABLE IF NOT EXISTS bio.genome (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
    source text NOT NULL,
    accession text NOT NULL,
    assembly_level text,
    release_date date,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_genome_source_accession
    ON bio.genome (source, accession);

-- Observations schema
CREATE TABLE IF NOT EXISTS obs.observation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id uuid REFERENCES core.taxon (id) ON DELETE SET NULL,
    source text NOT NULL,
    source_id text,
    observer text,
    observed_at timestamptz NOT NULL,
    location geography(Point, 4326),
    accuracy_m double precision,
    media jsonb NOT NULL DEFAULT '[]'::jsonb,
    notes text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_observation_taxon ON obs.observation (taxon_id);
CREATE INDEX IF NOT EXISTS idx_observation_location ON obs.observation USING GIST (location);

-- Telemetry schema
CREATE TABLE IF NOT EXISTS telemetry.device (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    slug text UNIQUE,
    taxon_id uuid REFERENCES core.taxon (id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'active',
    location geography(Point, 4326),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS telemetry.stream (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES telemetry.device (id) ON DELETE CASCADE,
    key text NOT NULL,
    unit text,
    description text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (device_id, key)
);

CREATE TABLE IF NOT EXISTS telemetry.sample (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id uuid NOT NULL REFERENCES telemetry.stream (id) ON DELETE CASCADE,
    recorded_at timestamptz NOT NULL,
    value_numeric double precision,
    value_text text,
    value_json jsonb,
    value_unit text,
    location geography(Point, 4326),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sample_stream_recorded_at
    ON telemetry.sample (stream_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_sample_location
    ON telemetry.sample USING GIST (location);

-- IP schema
CREATE TABLE IF NOT EXISTS ip.ip_asset (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    description text,
    taxon_id uuid REFERENCES core.taxon (id) ON DELETE SET NULL,
    created_by text,
    content_hash bytea,
    content_uri text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Ledger schema
CREATE TABLE IF NOT EXISTS ledger.hypergraph_anchor (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_asset_id uuid REFERENCES ip.ip_asset (id) ON DELETE CASCADE,
    sample_id uuid REFERENCES telemetry.sample (id) ON DELETE SET NULL,
    anchor_hash bytea NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    anchored_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ledger.bitcoin_ordinal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_asset_id uuid REFERENCES ip.ip_asset (id) ON DELETE CASCADE,
    content_hash bytea NOT NULL,
    inscription_id text NOT NULL,
    inscription_address text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    inscribed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (inscription_id)
);

CREATE TABLE IF NOT EXISTS ledger.solana_binding (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_asset_id uuid REFERENCES ip.ip_asset (id) ON DELETE CASCADE,
    mint_address text NOT NULL,
    token_account text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    bound_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mint_address)
);

-- App helper views
CREATE OR REPLACE VIEW app.v_taxon_with_traits AS
SELECT
    t.id,
    t.canonical_name,
    t.rank,
    t.common_name,
    t.description,
    t.source,
    COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'id', tr.id,
                'trait_name', tr.trait_name,
                'value_text', tr.value_text,
                'value_numeric', tr.value_numeric,
                'value_unit', tr.value_unit,
                'source', tr.source
            )
            ORDER BY tr.trait_name
        ) FILTER (WHERE tr.id IS NOT NULL),
        '[]'::jsonb
    ) AS traits
FROM core.taxon t
LEFT JOIN bio.taxon_trait tr ON tr.taxon_id = t.id
GROUP BY t.id;

CREATE OR REPLACE VIEW app.v_device_latest_samples AS
SELECT
    d.id AS device_id,
    d.name AS device_name,
    d.slug AS device_slug,
    s.id AS stream_id,
    s.key AS stream_key,
    s.unit AS stream_unit,
    latest.sample_id,
    latest.recorded_at,
    latest.value_numeric,
    latest.value_text,
    latest.value_json,
    latest.value_unit,
    latest.metadata AS sample_metadata,
    latest.location AS sample_location,
    d.location AS device_location
FROM telemetry.device d
JOIN telemetry.stream s ON s.device_id = d.id
JOIN LATERAL (
    SELECT
        sa.id AS sample_id,
        sa.recorded_at,
        sa.value_numeric,
        sa.value_text,
        sa.value_json,
        sa.value_unit,
        sa.metadata,
        sa.location
    FROM telemetry.sample sa
    WHERE sa.stream_id = s.id
    ORDER BY sa.recorded_at DESC
    LIMIT 1
) latest ON TRUE;

COMMIT;
