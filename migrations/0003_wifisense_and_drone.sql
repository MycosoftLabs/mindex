-- Migration: WiFi Sense and MycoDRONE capabilities
-- Version: 0003
-- Date: 2025-01-XX

-- ============================================================================
-- WiFi Sense Tables
-- ============================================================================

-- WiFi Sense device configuration
CREATE TABLE IF NOT EXISTS telemetry.wifisense_device (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id UUID NOT NULL REFERENCES telemetry.device(id) ON DELETE CASCADE,
  link_id VARCHAR(32) NOT NULL,
  channel SMALLINT,
  bandwidth SMALLINT,
  csi_format SMALLINT,
  num_antennas SMALLINT,
  num_subcarriers SMALLINT,
  calibration_data JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(device_id, link_id)
);

-- CSI raw data (time-series)
CREATE TABLE IF NOT EXISTS telemetry.wifisense_csi (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id UUID NOT NULL REFERENCES telemetry.device(id) ON DELETE CASCADE,
  link_id VARCHAR(32),
  timestamp_ns BIGINT NOT NULL,
  channel SMALLINT,
  rssi SMALLINT,
  csi_data BYTEA,  -- Compressed CSI samples
  csi_length INTEGER,
  csi_format SMALLINT,
  num_subcarriers SMALLINT,
  num_antennas SMALLINT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Presence events (derived)
CREATE TABLE IF NOT EXISTS telemetry.wifisense_presence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  zone_id VARCHAR(64),
  timestamp TIMESTAMPTZ NOT NULL,
  presence_type VARCHAR(32),  -- 'occupancy', 'motion', 'activity'
  confidence FLOAT,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tracks (multi-target)
CREATE TABLE IF NOT EXISTS telemetry.wifisense_track (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  track_id VARCHAR(64) NOT NULL,
  zone_id VARCHAR(64),
  position POINT,  -- PostGIS
  velocity FLOAT,
  activity_class VARCHAR(32),
  confidence FLOAT,
  first_seen TIMESTAMPTZ,
  last_seen TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pose data (Phase 2)
CREATE TABLE IF NOT EXISTS telemetry.wifisense_pose (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  track_id VARCHAR(64),
  timestamp TIMESTAMPTZ NOT NULL,
  keypoints JSONB,  -- {body_part: {x, y, z, confidence}}
  dense_uv JSONB,    -- DensePose UV coordinates
  confidence FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- MycoDRONE Tables
-- ============================================================================

-- Drone registry
CREATE TABLE IF NOT EXISTS telemetry.drone (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id UUID NOT NULL REFERENCES telemetry.device(id) ON DELETE CASCADE,
  drone_type VARCHAR(32),  -- 'mycodrone_v1'
  max_payload_kg FLOAT,
  max_range_km FLOAT,
  home_latitude FLOAT,
  home_longitude FLOAT,
  dock_id UUID,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(device_id)
);

-- Drone missions
CREATE TABLE IF NOT EXISTS telemetry.drone_mission (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  drone_id UUID NOT NULL REFERENCES telemetry.drone(id) ON DELETE CASCADE,
  mission_type VARCHAR(32),  -- 'deploy', 'retrieve', 'data_mule'
  target_device_id UUID REFERENCES telemetry.device(id) ON DELETE SET NULL,
  waypoint_lat FLOAT,
  waypoint_lon FLOAT,
  waypoint_alt FLOAT,
  status VARCHAR(32),  -- 'pending', 'in_progress', 'completed', 'failed'
  progress INTEGER,  -- 0-100
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Drone telemetry log
CREATE TABLE IF NOT EXISTS telemetry.drone_telemetry_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  drone_id UUID NOT NULL REFERENCES telemetry.drone(id) ON DELETE CASCADE,
  timestamp TIMESTAMPTZ NOT NULL,
  latitude FLOAT,
  longitude FLOAT,
  altitude_msl FLOAT,
  altitude_rel FLOAT,
  heading FLOAT,
  ground_speed FLOAT,
  battery_percent SMALLINT,
  battery_voltage FLOAT,
  flight_mode VARCHAR(32),
  mission_state VARCHAR(32),
  payload_latched BOOLEAN,
  payload_type VARCHAR(32),
  temp_c FLOAT,
  humidity_rh FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Docking stations
CREATE TABLE IF NOT EXISTS telemetry.dock (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(64) NOT NULL,
  latitude FLOAT NOT NULL,
  longitude FLOAT NOT NULL,
  altitude FLOAT,
  fiducial_id VARCHAR(32),
  charging_bays INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- Indexes
-- ============================================================================

-- WiFi Sense indexes
CREATE INDEX IF NOT EXISTS idx_wifisense_csi_device_time 
  ON telemetry.wifisense_csi(device_id, timestamp_ns DESC);
CREATE INDEX IF NOT EXISTS idx_wifisense_presence_zone_time 
  ON telemetry.wifisense_presence(zone_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_wifisense_track_zone 
  ON telemetry.wifisense_track(zone_id, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_wifisense_pose_track_time 
  ON telemetry.wifisense_pose(track_id, timestamp DESC);

-- MycoDRONE indexes
CREATE INDEX IF NOT EXISTS idx_drone_mission_status 
  ON telemetry.drone_mission(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drone_mission_drone 
  ON telemetry.drone_mission(drone_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drone_telemetry_time 
  ON telemetry.drone_telemetry_log(drone_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_drone_telemetry_mission 
  ON telemetry.drone_telemetry_log(mission_state, timestamp DESC);

-- ============================================================================
-- Views
-- ============================================================================

-- WiFi Sense status view
CREATE OR REPLACE VIEW app.v_wifisense_status AS
SELECT 
  d.id as device_id,
  d.name as device_name,
  wd.link_id,
  wd.channel,
  wd.bandwidth,
  COUNT(DISTINCT wc.id) as csi_samples_count,
  MAX(wc.timestamp_ns) as last_csi_timestamp,
  COUNT(DISTINCT wp.id) as presence_events_count,
  MAX(wp.timestamp) as last_presence_timestamp
FROM telemetry.device d
LEFT JOIN telemetry.wifisense_device wd ON d.id = wd.device_id
LEFT JOIN telemetry.wifisense_csi wc ON wd.device_id = wc.device_id
LEFT JOIN telemetry.wifisense_presence wp ON wd.link_id = wp.zone_id
GROUP BY d.id, d.name, wd.link_id, wd.channel, wd.bandwidth;

-- Drone status view
CREATE OR REPLACE VIEW app.v_drone_status AS
SELECT 
  d.id as drone_id,
  dev.name as drone_name,
  d.drone_type,
  d.max_payload_kg,
  d.home_latitude,
  d.home_longitude,
  dtl.latitude as current_latitude,
  dtl.longitude as current_longitude,
  dtl.altitude_rel as current_altitude,
  dtl.battery_percent,
  dtl.flight_mode,
  dtl.mission_state,
  dtl.payload_latched,
  dtl.payload_type,
  dm.status as active_mission_status,
  dm.progress as active_mission_progress,
  dtl.timestamp as last_telemetry_time
FROM telemetry.drone d
JOIN telemetry.device dev ON d.device_id = dev.id
LEFT JOIN LATERAL (
  SELECT * FROM telemetry.drone_telemetry_log
  WHERE drone_id = d.id
  ORDER BY timestamp DESC
  LIMIT 1
) dtl ON true
LEFT JOIN telemetry.drone_mission dm ON d.id = dm.drone_id 
  AND dm.status = 'in_progress'
ORDER BY dtl.timestamp DESC;

