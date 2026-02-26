-- Grounded Cognition Tables - February 17, 2026
-- Experience packets and thought objects for MYCA grounding pipeline

CREATE TABLE IF NOT EXISTS experience_packets (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  user_id TEXT,
  ground_truth JSONB NOT NULL,
  self_state JSONB,
  world_state JSONB,
  observation JSONB,
  uncertainty JSONB,
  provenance JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ep_session ON experience_packets(session_id);
CREATE INDEX IF NOT EXISTS idx_ep_created ON experience_packets(created_at DESC);

CREATE TABLE IF NOT EXISTS thought_objects (
  id TEXT PRIMARY KEY,
  ep_id TEXT REFERENCES experience_packets(id) ON DELETE SET NULL,
  session_id TEXT,
  claim TEXT NOT NULL,
  type TEXT NOT NULL,
  evidence_links JSONB DEFAULT '[]'::jsonb,
  confidence FLOAT,
  predicted_outcomes JSONB,
  risks JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_thoughts_ep ON thought_objects(ep_id);
CREATE INDEX IF NOT EXISTS idx_thoughts_session ON thought_objects(session_id);

-- Reflection logs for outcome tracking
CREATE TABLE IF NOT EXISTS reflection_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ep_id TEXT,
  session_id TEXT,
  response TEXT,
  prediction TEXT,
  actual TEXT,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reflection_ep ON reflection_logs(ep_id);
CREATE INDEX IF NOT EXISTS idx_reflection_session ON reflection_logs(session_id);
