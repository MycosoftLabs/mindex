-- Board and sensor instance identity for MycoBrain + NLM provenance
-- Date: May 31, 2026
BEGIN;

CREATE TABLE IF NOT EXISTS mycobrain.board_node (
    board_id text PRIMARY KEY,
    portal_device_id text,
    serial_number text,
    device_uuid uuid REFERENCES mycobrain.device(id) ON DELETE SET NULL,
    mac_suffix text,
    firmware_version text,
    board_role text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_board_node_portal ON mycobrain.board_node(portal_device_id);
CREATE INDEX IF NOT EXISTS idx_board_node_serial ON mycobrain.board_node(serial_number);

CREATE TABLE IF NOT EXISTS mycobrain.sensor_instance (
    sensor_id text PRIMARY KEY,
    board_id text NOT NULL REFERENCES mycobrain.board_node(board_id) ON DELETE CASCADE,
    portal_device_id text,
    sensor_slot text NOT NULL,
    peripheral_uid text NOT NULL,
    sensor_type text NOT NULL DEFAULT 'bme688',
    i2c_address int,
    chip_id text,
    bus text NOT NULL DEFAULT 'i2c0',
    status text NOT NULL DEFAULT 'unknown',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (board_id, sensor_slot),
    UNIQUE (peripheral_uid)
);

CREATE INDEX IF NOT EXISTS idx_sensor_instance_board ON mycobrain.sensor_instance(board_id);
CREATE INDEX IF NOT EXISTS idx_sensor_instance_portal ON mycobrain.sensor_instance(portal_device_id);

ALTER TABLE mycobrain.bme688_reading
    ADD COLUMN IF NOT EXISTS sensor_id text REFERENCES mycobrain.sensor_instance(sensor_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_bme688_sensor_recorded
    ON mycobrain.bme688_reading(sensor_id, recorded_at DESC);

CREATE SCHEMA IF NOT EXISTS nlm;

CREATE TABLE IF NOT EXISTS nlm.sensor_dataset_binding (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sensor_id text NOT NULL REFERENCES mycobrain.sensor_instance(sensor_id) ON DELETE CASCADE,
    dataset_id text NOT NULL,
    dataset_name text,
    purpose text NOT NULL DEFAULT 'nlm_training',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sensor_id, dataset_id)
);

CREATE INDEX IF NOT EXISTS idx_nlm_sensor_dataset ON nlm.sensor_dataset_binding(sensor_id);

COMMIT;
