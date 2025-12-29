-- MycoBrain Integration Migration
-- Adds device types, MDP protocol support, command queues, and NatureOS bridge tables
BEGIN;

-- ============================================================================
-- DEVICE TYPES & MYCOBRAIN SCHEMA
-- ============================================================================

-- Create mycobrain-specific schema for isolation
CREATE SCHEMA IF NOT EXISTS mycobrain;

-- Device type enumeration for all Mycosoft devices
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'device_type') THEN
        CREATE TYPE mycobrain.device_type AS ENUM (
            'mycobrain_v1',
            'mushroom_1', 
            'sporebase',
            'custom_sensor',
            'gateway'
        );
    END IF;
END$$;

-- MycoBrain device registry (extends telemetry.device)
CREATE TABLE IF NOT EXISTS mycobrain.device (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telemetry_device_id uuid UNIQUE REFERENCES telemetry.device(id) ON DELETE CASCADE,
    
    -- Device identification
    serial_number text UNIQUE NOT NULL,
    device_type mycobrain.device_type NOT NULL DEFAULT 'mycobrain_v1',
    hardware_revision text,
    
    -- Firmware tracking (Side-A, Side-B)
    firmware_version_a text,
    firmware_version_b text,
    firmware_updated_at timestamptz,
    
    -- API authentication
    api_key_hash bytea,
    api_key_prefix text,  -- First 8 chars for lookup
    
    -- LoRa configuration
    lora_dev_addr text,
    lora_frequency_mhz double precision,
    lora_spreading_factor int,
    lora_bandwidth_khz double precision,
    
    -- I2C sensor configuration (discovered addresses)
    i2c_addresses jsonb NOT NULL DEFAULT '[]'::jsonb,
    
    -- Analog channel configuration
    analog_channels jsonb NOT NULL DEFAULT '{
        "AI1": {"label": "Channel 1", "unit": "V", "min": 0, "max": 3.3},
        "AI2": {"label": "Channel 2", "unit": "V", "min": 0, "max": 3.3},
        "AI3": {"label": "Channel 3", "unit": "V", "min": 0, "max": 3.3},
        "AI4": {"label": "Channel 4", "unit": "V", "min": 0, "max": 3.3}
    }'::jsonb,
    
    -- MOSFET states
    mosfet_states jsonb NOT NULL DEFAULT '{
        "M1": false, "M2": false, "M3": false, "M4": false
    }'::jsonb,
    
    -- Power monitoring
    usb_power_connected boolean DEFAULT false,
    battery_voltage double precision,
    power_state text DEFAULT 'unknown',
    
    -- Telemetry configuration
    telemetry_interval_ms int DEFAULT 5000,
    last_seen_at timestamptz,
    last_sequence_number int DEFAULT 0,
    
    -- Owner/location binding
    owner_id uuid,
    location_name text,
    purpose text,
    
    -- Metadata
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mycobrain_device_serial ON mycobrain.device(serial_number);
CREATE INDEX IF NOT EXISTS idx_mycobrain_device_api_prefix ON mycobrain.device(api_key_prefix);
CREATE INDEX IF NOT EXISTS idx_mycobrain_device_type ON mycobrain.device(device_type);
CREATE INDEX IF NOT EXISTS idx_mycobrain_device_last_seen ON mycobrain.device(last_seen_at DESC);

-- ============================================================================
-- MDP V1 PROTOCOL MESSAGES
-- ============================================================================

-- Message types for MDP v1
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'mdp_message_type') THEN
        CREATE TYPE mycobrain.mdp_message_type AS ENUM (
            'telemetry',
            'command',
            'event',
            'ack',
            'nack',
            'heartbeat',
            'discovery'
        );
    END IF;
END$$;

-- Raw MDP frame storage for debugging and replay
CREATE TABLE IF NOT EXISTS mycobrain.mdp_frame (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    
    -- Frame metadata
    sequence_number int NOT NULL,
    message_type mycobrain.mdp_message_type NOT NULL,
    direction text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    
    -- Raw frame data
    raw_cobs_frame bytea,
    decoded_payload jsonb NOT NULL,
    crc16_valid boolean NOT NULL DEFAULT true,
    
    -- Timestamps
    device_timestamp_ms bigint,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mdp_frame_device_seq 
    ON mycobrain.mdp_frame(device_id, sequence_number DESC);
CREATE INDEX IF NOT EXISTS idx_mdp_frame_received 
    ON mycobrain.mdp_frame(received_at DESC);

-- Partition frames by month for performance (optional, can be enabled later)
-- CREATE TABLE IF NOT EXISTS mycobrain.mdp_frame_y2024m12 
--     PARTITION OF mycobrain.mdp_frame FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');

-- ============================================================================
-- COMMAND QUEUE (BI-DIRECTIONAL CONTROL)
-- ============================================================================

-- Command status tracking
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'command_status') THEN
        CREATE TYPE mycobrain.command_status AS ENUM (
            'pending',
            'sent',
            'acknowledged',
            'failed',
            'expired',
            'cancelled'
        );
    END IF;
END$$;

-- Command queue for downlink messages
CREATE TABLE IF NOT EXISTS mycobrain.command_queue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    
    -- Command details
    command_type text NOT NULL,
    command_payload jsonb NOT NULL,
    priority int NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    
    -- MDP framing
    sequence_number int,
    
    -- Status tracking
    status mycobrain.command_status NOT NULL DEFAULT 'pending',
    retry_count int NOT NULL DEFAULT 0,
    max_retries int NOT NULL DEFAULT 3,
    
    -- Timing
    created_at timestamptz NOT NULL DEFAULT now(),
    scheduled_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    acked_at timestamptz,
    expires_at timestamptz DEFAULT (now() + interval '1 hour'),
    
    -- Response tracking
    response_payload jsonb,
    error_message text,
    
    -- Requestor tracking
    requested_by text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_command_queue_device_pending 
    ON mycobrain.command_queue(device_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_command_queue_scheduled 
    ON mycobrain.command_queue(scheduled_at) WHERE status = 'pending';

-- ============================================================================
-- SENSOR READINGS (HIGH-FREQUENCY TELEMETRY)
-- ============================================================================

-- BME688 environmental sensor readings
CREATE TABLE IF NOT EXISTS mycobrain.bme688_reading (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    stream_id uuid REFERENCES telemetry.stream(id) ON DELETE SET NULL,
    
    -- Sensor identification
    chip_id text,
    i2c_address int,
    
    -- Environmental readings
    temperature_c double precision,
    humidity_percent double precision,
    pressure_hpa double precision,
    gas_resistance_ohms double precision,
    iaq_index double precision,
    
    -- Calculated values
    altitude_m double precision,
    dew_point_c double precision,
    
    -- Timestamps
    recorded_at timestamptz NOT NULL,
    device_timestamp_ms bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bme688_device_recorded 
    ON mycobrain.bme688_reading(device_id, recorded_at DESC);

-- Analog channel readings
CREATE TABLE IF NOT EXISTS mycobrain.analog_reading (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    stream_id uuid REFERENCES telemetry.stream(id) ON DELETE SET NULL,
    
    -- Channel identification
    channel text NOT NULL CHECK (channel IN ('AI1', 'AI2', 'AI3', 'AI4')),
    
    -- Reading values
    raw_adc_count int,
    voltage double precision NOT NULL,
    calibrated_value double precision,
    calibrated_unit text,
    
    -- Timestamps
    recorded_at timestamptz NOT NULL,
    device_timestamp_ms bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analog_device_channel_recorded 
    ON mycobrain.analog_reading(device_id, channel, recorded_at DESC);

-- ============================================================================
-- NATUREOS / MYCORRHIZAE PROTOCOL BRIDGE
-- ============================================================================

-- NatureOS widget configurations
CREATE TABLE IF NOT EXISTS mycobrain.natureos_widget (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    
    -- Widget configuration
    widget_type text NOT NULL DEFAULT 'mycobrain_dashboard',
    display_name text NOT NULL,
    layout_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Data binding
    bound_streams jsonb NOT NULL DEFAULT '[]'::jsonb,
    refresh_interval_ms int DEFAULT 5000,
    
    -- Access control
    visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared', 'public')),
    shared_with jsonb NOT NULL DEFAULT '[]'::jsonb,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Mycorrhizae Protocol channel subscriptions
CREATE TABLE IF NOT EXISTS mycobrain.mycorrhizae_subscription (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Channel definition
    channel_name text NOT NULL,
    channel_type text NOT NULL CHECK (channel_type IN ('device', 'aggregate', 'computed')),
    
    -- Source binding
    device_id uuid REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    stream_pattern text,  -- Regex pattern for stream matching
    
    -- Subscriber info
    subscriber_type text NOT NULL CHECK (subscriber_type IN ('natureos', 'mas_agent', 'external')),
    subscriber_endpoint text,
    subscriber_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Protocol settings
    protocol_version text NOT NULL DEFAULT 'v1',
    format text NOT NULL DEFAULT 'ndjson' CHECK (format IN ('ndjson', 'cbor', 'protobuf')),
    
    -- Status
    active boolean NOT NULL DEFAULT true,
    last_publish_at timestamptz,
    publish_count bigint DEFAULT 0,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mycorrhizae_channel 
    ON mycobrain.mycorrhizae_subscription(channel_name);
CREATE INDEX IF NOT EXISTS idx_mycorrhizae_device 
    ON mycobrain.mycorrhizae_subscription(device_id) WHERE device_id IS NOT NULL;

-- ============================================================================
-- AUTOMATION & THRESHOLDS
-- ============================================================================

-- Threshold-based automation rules
CREATE TABLE IF NOT EXISTS mycobrain.automation_rule (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES mycobrain.device(id) ON DELETE CASCADE,
    
    -- Rule definition
    name text NOT NULL,
    description text,
    enabled boolean NOT NULL DEFAULT true,
    
    -- Trigger condition
    trigger_stream text NOT NULL,
    trigger_operator text NOT NULL CHECK (trigger_operator IN ('gt', 'lt', 'gte', 'lte', 'eq', 'neq', 'between')),
    trigger_value double precision NOT NULL,
    trigger_value_high double precision,  -- For 'between' operator
    trigger_duration_ms int DEFAULT 0,     -- Debounce duration
    
    -- Action to take
    action_type text NOT NULL CHECK (action_type IN ('mosfet_on', 'mosfet_off', 'mosfet_toggle', 'alert', 'webhook', 'command')),
    action_target text,  -- MOSFET number, webhook URL, etc.
    action_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Cooldown
    cooldown_ms int DEFAULT 60000,
    last_triggered_at timestamptz,
    trigger_count bigint DEFAULT 0,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================
-- HELPER VIEWS
-- ============================================================================

-- Device status overview
CREATE OR REPLACE VIEW mycobrain.v_device_status AS
SELECT 
    d.id,
    d.serial_number,
    d.device_type::text,
    d.firmware_version_a,
    d.firmware_version_b,
    d.mosfet_states,
    d.usb_power_connected,
    d.battery_voltage,
    d.power_state,
    d.telemetry_interval_ms,
    d.last_seen_at,
    d.location_name,
    d.purpose,
    td.name as telemetry_device_name,
    td.status as telemetry_status,
    ST_AsGeoJSON(td.location::geometry) as location_geojson,
    CASE 
        WHEN d.last_seen_at > now() - interval '2 minutes' THEN 'online'
        WHEN d.last_seen_at > now() - interval '10 minutes' THEN 'stale'
        ELSE 'offline'
    END as connectivity_status,
    (SELECT count(*) FROM mycobrain.command_queue cq 
     WHERE cq.device_id = d.id AND cq.status = 'pending') as pending_commands
FROM mycobrain.device d
LEFT JOIN telemetry.device td ON td.id = d.telemetry_device_id;

-- Latest sensor readings per device
CREATE OR REPLACE VIEW mycobrain.v_latest_readings AS
SELECT DISTINCT ON (d.id)
    d.id as device_id,
    d.serial_number,
    b.temperature_c,
    b.humidity_percent,
    b.pressure_hpa,
    b.gas_resistance_ohms,
    b.iaq_index,
    b.recorded_at as bme_recorded_at,
    (
        SELECT jsonb_object_agg(ar.channel, ar.voltage ORDER BY ar.channel)
        FROM mycobrain.analog_reading ar
        WHERE ar.device_id = d.id
        AND ar.recorded_at = (
            SELECT MAX(ar2.recorded_at) 
            FROM mycobrain.analog_reading ar2 
            WHERE ar2.device_id = d.id
        )
    ) as analog_voltages
FROM mycobrain.device d
LEFT JOIN mycobrain.bme688_reading b ON b.device_id = d.id
ORDER BY d.id, b.recorded_at DESC NULLS LAST;

-- ============================================================================
-- TRIGGER FOR UPDATED_AT
-- ============================================================================

CREATE OR REPLACE FUNCTION mycobrain.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t text;
BEGIN
    FOR t IN SELECT unnest(ARRAY['device', 'natureos_widget', 'mycorrhizae_subscription', 'automation_rule'])
    LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS trg_%s_updated_at ON mycobrain.%s;
            CREATE TRIGGER trg_%s_updated_at 
            BEFORE UPDATE ON mycobrain.%s
            FOR EACH ROW EXECUTE FUNCTION mycobrain.update_updated_at();
        ', t, t, t, t);
    END LOOP;
END$$;

-- ============================================================================
-- GRANTS (adjust roles as needed)
-- ============================================================================

-- Grant usage on schema
-- GRANT USAGE ON SCHEMA mycobrain TO mindex_api;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mycobrain TO mindex_api;
-- GRANT USAGE ON ALL SEQUENCES IN SCHEMA mycobrain TO mindex_api;

COMMIT;


