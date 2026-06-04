-- Network storage federation (MINDEX handoff Request 006).
BEGIN;

CREATE SCHEMA IF NOT EXISTS network;

CREATE TABLE IF NOT EXISTS network.storage_node (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL DEFAULT 'nas',
    label TEXT NOT NULL,
    host TEXT,
    region TEXT,
    capacity_bytes BIGINT,
    used_bytes BIGINT DEFAULT 0,
    owner TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS network.shard (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_node_id UUID REFERENCES network.storage_node(id) ON DELETE CASCADE,
    shard_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO network.storage_node (kind, label, host, region, capacity_bytes, used_bytes, owner, last_seen_at, metadata)
SELECT 'nas', 'MINDEX NAS', '192.168.0.105', 'lab', 103809024000, 10380902400, 'mycosoft', NOW(),
       '{"mount":"/mnt/nas/mindex","role":"primary_blob_store"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM network.storage_node WHERE label = 'MINDEX NAS');

INSERT INTO network.storage_node (kind, label, host, region, capacity_bytes, used_bytes, owner, last_seen_at, metadata)
SELECT 'edge', 'Sandbox VM', '192.168.0.187', 'sandbox', NULL, NULL, 'mycosoft', NOW(),
       '{"services":["website","mycobrain"]}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM network.storage_node WHERE label = 'Sandbox VM');

GRANT USAGE ON SCHEMA network TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA network TO mindex;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA network TO mindex;

COMMIT;
