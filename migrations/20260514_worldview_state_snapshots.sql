CREATE SCHEMA IF NOT EXISTS worldview;

CREATE TABLE IF NOT EXISTS worldview.worldview_state_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    region JSONB NOT NULL DEFAULT '{}'::jsonb,
    world_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    sources_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_freshness JSONB NOT NULL DEFAULT '{}'::jsonb,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    avani_verdict TEXT NOT NULL DEFAULT 'allow',
    audit_trail_id TEXT,
    entry_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_captured_at
    ON worldview.worldview_state_snapshots (captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_region
    ON worldview.worldview_state_snapshots USING GIN (region);

CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_avani_verdict
    ON worldview.worldview_state_snapshots (avani_verdict);

CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_audit_trail_id
    ON worldview.worldview_state_snapshots (audit_trail_id);
