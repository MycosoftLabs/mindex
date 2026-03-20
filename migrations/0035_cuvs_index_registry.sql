-- Migration: 0035_cuvs_index_registry.sql
-- Date: March 20, 2026
-- Purpose: Registry for GPU-accelerated vector indexes (cuVS)
--
-- Tracks which cuVS indexes exist, their configurations, build status,
-- and links to MICA Merkle roots for cryptographic verification of
-- index state. Each index build produces a new MICA root_record so
-- that search results can be verified against a specific index version.
--
-- Interfaces with:
--   - mica.root_record (last_root_hash → spatial root)
--   - mica.mutable_head (cuvs.{index_name} → latest root)
--   - fci_signal_embeddings (768-dim bioelectric patterns)
--   - nlm.nature_embeddings (16-dim anomaly scoring)
--   - images (512-dim image similarity)

BEGIN;

-- ============================================================================
-- VECTOR INDEX REGISTRY
-- ============================================================================

CREATE TABLE IF NOT EXISTS mica.vector_index_registry (
    index_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    index_name          TEXT UNIQUE NOT NULL,          -- e.g. 'fci_signals', 'nlm_nature'
    source_table        TEXT NOT NULL,                 -- e.g. 'fci_signal_embeddings'
    source_column       TEXT NOT NULL,                 -- e.g. 'embedding'
    id_column           TEXT NOT NULL DEFAULT 'id',
    dimensions          INTEGER NOT NULL,
    index_type          TEXT NOT NULL DEFAULT 'ivf_pq', -- ivf_pq, ivf_flat, cagra
    metric              TEXT NOT NULL DEFAULT 'cosine', -- cosine, l2, inner_product

    -- Index build parameters
    index_params        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Current state
    vector_count        BIGINT DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending, building, ready, error, stale

    -- Build history
    last_build_at       TIMESTAMPTZ,
    last_build_time_ms  INTEGER,                       -- Build duration in milliseconds
    build_count         INTEGER DEFAULT 0,

    -- MICA Merkle linkage
    last_root_hash      BYTEA,                         -- Latest mica.root_record hash for this index

    -- Persistence
    storage_path        TEXT,                          -- NAS path to persisted index files
    storage_size_bytes  BIGINT DEFAULT 0,

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vir_status ON mica.vector_index_registry (status);
CREATE INDEX IF NOT EXISTS idx_vir_source ON mica.vector_index_registry (source_table);

-- ============================================================================
-- VECTOR INDEX BUILD LOG (audit trail)
-- ============================================================================

CREATE TABLE IF NOT EXISTS mica.vector_index_build_log (
    build_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    index_id            UUID NOT NULL REFERENCES mica.vector_index_registry(index_id),
    index_name          TEXT NOT NULL,

    -- Build details
    build_type          TEXT NOT NULL DEFAULT 'full',   -- full, incremental, streaming
    vector_count        BIGINT NOT NULL DEFAULT 0,
    build_time_ms       INTEGER,

    -- Backend
    backend             TEXT NOT NULL DEFAULT 'numpy',  -- cuvs, numpy, pgvector
    gpu_device          TEXT,                           -- e.g. 'NVIDIA A100'
    gpu_vram_used_mb    REAL,

    -- MICA linkage
    root_hash           BYTEA,                         -- mica.root_record hash for this build
    previous_root_hash  BYTEA,                         -- Previous root (chain link)

    -- Status
    status              TEXT NOT NULL DEFAULT 'success', -- success, error
    error_message       TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vibl_index ON mica.vector_index_build_log (index_id, created_at DESC);

-- ============================================================================
-- STATIC CONSTRAINT SET REGISTRY
-- ============================================================================

CREATE TABLE IF NOT EXISTS mica.static_constraint_registry (
    constraint_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain              TEXT NOT NULL,                  -- taxonomy, compounds, devices, etc.
    name                TEXT NOT NULL,                  -- e.g. 'mindex_taxonomy'

    -- Constraint data
    sequence_count      BIGINT NOT NULL DEFAULT 0,
    version_hash        TEXT NOT NULL,                  -- SHA-256 of sorted sequences

    -- MAS sync state
    pushed_to_mas       BOOLEAN DEFAULT FALSE,
    mas_index_status    TEXT DEFAULT 'pending',         -- pending, building, ready, error

    -- MICA linkage
    event_hash          BYTEA,                         -- mica.event_object hash

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (domain, version_hash)
);

CREATE INDEX IF NOT EXISTS idx_scr_domain ON mica.static_constraint_registry (domain);

-- ============================================================================
-- Seed default index configurations
-- ============================================================================

INSERT INTO mica.vector_index_registry (index_name, source_table, source_column, id_column, dimensions, index_type, metric, index_params)
VALUES
    ('fci_signals', 'fci_signal_embeddings', 'embedding', 'id', 768, 'ivf_pq', 'cosine',
     '{"nlist": 256, "nprobe": 32, "pq_dim": 64, "pq_bits": 8}'::jsonb),
    ('nlm_nature', 'nlm.nature_embeddings', 'embedding', 'id', 16, 'ivf_flat', 'cosine',
     '{"nlist": 16, "nprobe": 8}'::jsonb),
    ('image_similarity', 'images', 'embedding', 'id', 512, 'cagra', 'cosine',
     '{"nlist": 128, "nprobe": 16}'::jsonb)
ON CONFLICT (index_name) DO NOTHING;

COMMIT;
