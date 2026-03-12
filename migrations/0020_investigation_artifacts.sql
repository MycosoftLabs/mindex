-- Investigation Artifacts and Evidence-Backed Analysis - March 10, 2026
-- OpenPlanter-style investigation schema per INTEGRATION_CONTRACTS_CANONICAL_MAR10_2026

CREATE SCHEMA IF NOT EXISTS investigation;

CREATE TABLE IF NOT EXISTS investigation.investigation_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  description TEXT,
  source TEXT NOT NULL,
  source_id TEXT,
  artifacts JSONB DEFAULT '[]'::jsonb,
  sources JSONB DEFAULT '[]'::jsonb,
  agent_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inv_artifacts_source ON investigation.investigation_artifacts(source);
CREATE INDEX IF NOT EXISTS idx_inv_artifacts_created ON investigation.investigation_artifacts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_artifacts_agent ON investigation.investigation_artifacts(agent_id);

CREATE TABLE IF NOT EXISTS investigation.evidence_relationships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id TEXT NOT NULL,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  relationship_type TEXT NOT NULL,
  confidence FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_entity ON investigation.evidence_relationships(entity_id);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON investigation.evidence_relationships(relationship_type);
