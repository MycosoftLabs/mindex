-- MINDEX App Full Overhaul — May 03, 2026
-- Idempotent: safe to re-run on VM 189 after review.
-- Aligns with mindex_api routers: ledger, network, devices_inventory, integrity.

BEGIN;

CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS network;
CREATE SCHEMA IF NOT EXISTS devices;
CREATE SCHEMA IF NOT EXISTS synthetic;

-- ---------------------------------------------------------------------------
-- Ledger: hypergraph DAG + anchors (matches mindex_api/ledger/dag.py + ledger.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ledger.dag_node (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash bytea NOT NULL,
    parent_hashes bytea[] NOT NULL DEFAULT '{}'::bytea[],
    epoch bigint NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ledger_dag_node_content_hash
    ON ledger.dag_node (content_hash);
CREATE INDEX IF NOT EXISTS idx_ledger_dag_node_epoch
    ON ledger.dag_node (epoch DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS ledger.anchor (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    content_hash bytea NOT NULL,
    tier text NOT NULL,
    solana_signature text,
    ordinal_inscription_id text,
    platform_one_ref text,
    hypergraph_node_id uuid REFERENCES ledger.dag_node (id) ON DELETE SET NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ledger_anchor_entity
    ON ledger.anchor (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_ledger_anchor_created
    ON ledger.anchor (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_anchor_tier
    ON ledger.anchor (tier);

-- ---------------------------------------------------------------------------
-- Network: storage federation (matches routers/network.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS network.storage_node (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL,
    label text NOT NULL,
    host text,
    region text,
    capacity_bytes bigint,
    used_bytes bigint,
    owner text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_network_storage_node_kind_label
    ON network.storage_node (kind, label);

CREATE TABLE IF NOT EXISTS network.shard (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_node_id uuid NOT NULL REFERENCES network.storage_node (id) ON DELETE CASCADE,
    shard_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_network_shard_node_key UNIQUE (storage_node_id, shard_key)
);

CREATE TABLE IF NOT EXISTS network.access_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash bytea,
    accessed_by_user_id uuid,
    accessed_at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL,
    source_ip text
);

CREATE INDEX IF NOT EXISTS idx_network_access_event_time
    ON network.access_event (accessed_at DESC);

-- ---------------------------------------------------------------------------
-- Devices inventory + suggestions (matches routers/devices_inventory.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices.inventory (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_key text NOT NULL,
    device_type text NOT NULL,
    serial text,
    status text NOT NULL DEFAULT 'unknown',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_devices_inventory_device_key UNIQUE (device_key)
);

CREATE INDEX IF NOT EXISTS idx_devices_inventory_status
    ON devices.inventory (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS devices.deployment_suggestion (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inventory_id uuid REFERENCES devices.inventory (id) ON DELETE SET NULL,
    rationale text,
    priority integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_devices_suggestion_status
    ON devices.deployment_suggestion (status, priority DESC, created_at DESC);

-- ---------------------------------------------------------------------------
-- Synthetic datasets (plan)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS synthetic.dataset (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    source_query text,
    generator_agent text,
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_path text,
    sample_count bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Canonical content-hash chain per record (plan: core.content_hash table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.content_hash (
    hash_index bigserial PRIMARY KEY,
    record_id uuid NOT NULL,
    table_name text NOT NULL,
    content_hash bytea NOT NULL,
    signing_key_fingerprint text,
    signed_at timestamptz,
    signature bytea,
    prev_hash bytea,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_core_content_hash_record
    ON core.content_hash (table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_core_content_hash_created
    ON core.content_hash (created_at DESC);

-- ---------------------------------------------------------------------------
-- Row-level content_hash on hot tables (used by integrity + mark-ip flows)
-- ---------------------------------------------------------------------------
ALTER TABLE core.taxon ADD COLUMN IF NOT EXISTS content_hash bytea;
ALTER TABLE core.taxon ADD COLUMN IF NOT EXISTS content_hashed_at timestamptz;
ALTER TABLE bio.genome ADD COLUMN IF NOT EXISTS content_hash bytea;
ALTER TABLE bio.genome ADD COLUMN IF NOT EXISTS content_hashed_at timestamptz;
ALTER TABLE bio.taxon_compound ADD COLUMN IF NOT EXISTS content_hash bytea;
ALTER TABLE bio.taxon_compound ADD COLUMN IF NOT EXISTS content_hashed_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_core_taxon_content_hash
    ON core.taxon (content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bio_genome_content_hash
    ON bio.genome (content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bio_taxon_compound_content_hash
    ON bio.taxon_compound (content_hash) WHERE content_hash IS NOT NULL;

COMMIT;
