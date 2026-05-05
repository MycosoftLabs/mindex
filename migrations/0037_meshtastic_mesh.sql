-- Meshtastic mesh telemetry (May 03, 2026)
-- Schema: meshtastic — CREP / Earth Simulator / NatureOS / FUSARIUM / MAS bridge ingest

BEGIN;

CREATE SCHEMA IF NOT EXISTS meshtastic;

COMMENT ON SCHEMA meshtastic IS
    'Meshtastic mesh nodes, packets, observers (gateways), and route aggregates — ingested via MQTT bridge and optional MycoBrain LoRa gateway.';

-- Known mesh nodes (dedupe by node_id hex without leading !)
CREATE TABLE IF NOT EXISTS meshtastic.nodes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         text NOT NULL UNIQUE,
    long_name       text,
    short_name      text,
    hw_model        text,
    role            text,
    position        geography(Point, 4326),
    last_heard_at   timestamptz,
    battery_pct     double precision,
    voltage         double precision,
    channel_util    double precision,
    air_util_tx     double precision,
    firmware        text,
    region          text,
    modem_preset    text,
    is_licensed     boolean DEFAULT false,
    is_observer     boolean DEFAULT false,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_meshtastic_nodes_last_heard
    ON meshtastic.nodes (last_heard_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_meshtastic_nodes_position
    ON meshtastic.nodes USING GIST (position);

-- Raw / decoded packets (high volume — partition by month optional later)
CREATE TABLE IF NOT EXISTS meshtastic.packets (
    id              bigserial PRIMARY KEY,
    packet_uid      text UNIQUE,
    from_node_id    text,
    to_node_id      text,
    gateway_node_id text,
    channel         text,
    port_num        text,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_text    text,
    rx_time         timestamptz NOT NULL DEFAULT now(),
    rx_rssi         double precision,
    rx_snr          double precision,
    hop_limit       int,
    hop_start       int,
    want_ack        boolean DEFAULT false,
    via_mqtt        boolean NOT NULL DEFAULT true,
    topic           text,
    raw_b64         text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_meshtastic_packets_rx_time
    ON meshtastic.packets (rx_time DESC);
CREATE INDEX IF NOT EXISTS idx_meshtastic_packets_from
    ON meshtastic.packets (from_node_id);
CREATE INDEX IF NOT EXISTS idx_meshtastic_packets_gateway
    ON meshtastic.packets (gateway_node_id);

-- Gateways / observers that heard traffic
CREATE TABLE IF NOT EXISTS meshtastic.observers (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observer_id     text NOT NULL,
    node_id         text,
    position        geography(Point, 4326),
    region          text,
    gateway_kind    text NOT NULL CHECK (gateway_kind IN ('mqtt', 'lora', 'mycobrain')),
    online          boolean NOT NULL DEFAULT true,
    pkts_per_min    double precision,
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (observer_id, gateway_kind)
);

CREATE INDEX IF NOT EXISTS idx_meshtastic_observers_seen
    ON meshtastic.observers (last_seen_at DESC);

-- Aggregated hop / link stats
CREATE TABLE IF NOT EXISTS meshtastic.routes (
    id              bigserial PRIMARY KEY,
    from_node_id    text NOT NULL,
    to_node_id      text NOT NULL,
    hops            int,
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    packet_count    bigint NOT NULL DEFAULT 1,
    avg_snr         double precision,
    UNIQUE (from_node_id, to_node_id)
);

CREATE INDEX IF NOT EXISTS idx_meshtastic_routes_last_seen
    ON meshtastic.routes (last_seen_at DESC);

COMMIT;
