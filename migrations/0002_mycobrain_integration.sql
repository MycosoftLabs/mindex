BEGIN;

-- MycoBrain Integration Migration

-- Extend telemetry.device with MycoBrain fields
ALTER TABLE telemetry.device
    ADD COLUMN IF NOT EXISTS device_type text DEFAULT 'generic',
    ADD COLUMN IF NOT EXISTS serial_number text UNIQUE,
    ADD COLUMN IF NOT EXISTS firmware_version text,
    ADD COLUMN IF NOT EXISTS api_key_hash text,
    ADD COLUMN IF NOT EXISTS power_status text DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS last_seen_at timestamptz,
    ADD COLUMN IF NOT EXISTS mdp_sequence_number bigint DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_device_type ON telemetry.device (device_type);
CREATE INDEX IF NOT EXISTS idx_device_serial_number ON telemetry.device (serial_number);
CREATE INDEX IF NOT EXISTS idx_device_api_key_hash ON telemetry.device (api_key_hash);
CREATE INDEX IF NOT EXISTS idx_device_last_seen ON telemetry.device (last_seen_at DESC);

-- MycoBrain-specific metadata
CREATE TABLE IF NOT EXISTS telemetry.mycobrain_device (
    id uuid PRIMARY KEY REFERENCES telemetry.device (id) ON DELETE CASCADE,

    i2c_addresses jsonb NOT NULL DEFAULT '[]'::jsonb,
    analog_channels jsonb NOT NULL DEFAULT '{}'::jsonb,
    mosfet_states jsonb NOT NULL DEFAULT '{}'::jsonb,

    side_a_firmware_version text,
    side_b_firmware_version text,

    lora_configured boolean DEFAULT false,
    lora_frequency_mhz double precision,
    lora_spreading_factor integer,
    lora_bandwidth_khz integer,

    gateway_enabled boolean DEFAULT false,

    mdp_last_sequence bigint DEFAULT 0,
    mdp_last_telemetry_at timestamptz,
    mdp_last_command_at timestamptz,

    telemetry_interval_seconds integer DEFAULT 60,
    command_timeout_seconds integer DEFAULT 30,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mycobrain_last_telemetry ON telemetry.mycobrain_device (mdp_last_telemetry_at DESC);

-- Commands for downlink (MAS/NatureOS -> device)
CREATE TABLE IF NOT EXISTS telemetry.device_command (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES telemetry.device (id) ON DELETE CASCADE,

    command_type text NOT NULL,
    command_id text NOT NULL,
    mdp_sequence_number bigint,

    payload jsonb NOT NULL DEFAULT '{}'::jsonb,

    status text NOT NULL DEFAULT 'pending',
    priority integer DEFAULT 0,

    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    acknowledged_at timestamptz,
    expires_at timestamptz,

    response jsonb,
    error_message text,

    retry_count integer DEFAULT 0,
    max_retries integer DEFAULT 3,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_device_command_device_status ON telemetry.device_command (device_id, status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_device_command_expires ON telemetry.device_command (expires_at) WHERE status = 'pending';

-- Setpoints / schedules
CREATE TABLE IF NOT EXISTS telemetry.device_setpoint (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES telemetry.device (id) ON DELETE CASCADE,

    setpoint_type text NOT NULL,
    target_value_numeric double precision,
    target_value_text text,
    target_value_json jsonb,

    schedule_cron text,
    schedule_timezone text DEFAULT 'UTC',
    active boolean DEFAULT true,

    min_value double precision,
    max_value double precision,
    hysteresis double precision,

    effective_from timestamptz NOT NULL DEFAULT now(),
    effective_until timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_device_setpoint_device_active ON telemetry.device_setpoint (device_id, active, effective_from, effective_until);

-- Raw MDP telemetry log for idempotency
CREATE TABLE IF NOT EXISTS telemetry.mdp_telemetry_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES telemetry.device (id) ON DELETE CASCADE,

    mdp_sequence_number bigint NOT NULL,
    mdp_timestamp timestamptz NOT NULL,
    mdp_message_type text NOT NULL,

    raw_payload jsonb NOT NULL,

    processed boolean DEFAULT false,
    processed_at timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (device_id, mdp_sequence_number, mdp_timestamp)
);

CREATE INDEX IF NOT EXISTS idx_mdp_telemetry_device_sequence ON telemetry.mdp_telemetry_log (device_id, mdp_sequence_number DESC);
CREATE INDEX IF NOT EXISTS idx_mdp_telemetry_unprocessed ON telemetry.mdp_telemetry_log (processed, created_at) WHERE processed = false;

-- Views
CREATE OR REPLACE VIEW app.v_mycobrain_status AS
SELECT
    d.id AS device_id,
    d.name AS device_name,
    d.slug AS device_slug,
    d.status AS device_status,
    d.serial_number,
    d.firmware_version,
    d.power_status,
    d.last_seen_at,
    mb.side_a_firmware_version,
    mb.side_b_firmware_version,
    mb.lora_configured,
    mb.mdp_last_telemetry_at,
    mb.mdp_last_command_at,
    mb.telemetry_interval_seconds,
    mb.i2c_addresses,
    mb.analog_channels,
    mb.mosfet_states,
    (SELECT count(*) FROM telemetry.device_command c WHERE c.device_id = d.id AND c.status = 'pending') AS pending_commands_count,
    EXTRACT(EPOCH FROM (now() - mb.mdp_last_telemetry_at)) AS seconds_since_last_telemetry
FROM telemetry.device d
LEFT JOIN telemetry.mycobrain_device mb ON mb.id = d.id
WHERE d.device_type = 'mycobrain_v1';

COMMIT;
