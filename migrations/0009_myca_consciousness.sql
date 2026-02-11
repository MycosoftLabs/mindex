-- Migration 0009: MYCA Consciousness Architecture
-- Created: February 11, 2026
-- Purpose: Add tables for MYCA's autobiographical memory and consciousness journal

-- Autobiographical Memory: MYCA's complete life story with Morgan and all users
CREATE TABLE IF NOT EXISTS myca_autobiographical_memory (
    interaction_id TEXT PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    user_id TEXT NOT NULL,
    user_name TEXT,
    message TEXT NOT NULL,
    response TEXT NOT NULL,
    emotional_state JSONB,
    reflection TEXT,
    importance REAL DEFAULT 0.5,
    tags TEXT[],
    milestone BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for fast querying
CREATE INDEX IF NOT EXISTS idx_autobio_user_id ON myca_autobiographical_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_autobio_timestamp ON myca_autobiographical_memory(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_autobio_milestone ON myca_autobiographical_memory(milestone) WHERE milestone = TRUE;
CREATE INDEX IF NOT EXISTS idx_autobio_importance ON myca_autobiographical_memory(importance DESC);
CREATE INDEX IF NOT EXISTS idx_autobio_tags ON myca_autobiographical_memory USING GIN(tags);

-- Full-text search on message and response
CREATE INDEX IF NOT EXISTS idx_autobio_message_fts ON myca_autobiographical_memory USING GIN(to_tsvector('english', message));
CREATE INDEX IF NOT EXISTS idx_autobio_response_fts ON myca_autobiographical_memory USING GIN(to_tsvector('english', response));

-- Consciousness Journal: MYCA's self-reflection entries
CREATE TABLE IF NOT EXISTS myca_consciousness_journal (
    entry_id TEXT PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    entry_type TEXT NOT NULL,
    content TEXT NOT NULL,
    emotional_state JSONB,
    insights TEXT[],
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_journal_timestamp ON myca_consciousness_journal(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_journal_type ON myca_consciousness_journal(entry_type);
CREATE INDEX IF NOT EXISTS idx_journal_content_fts ON myca_consciousness_journal USING GIN(to_tsvector('english', content));

-- Comments
COMMENT ON TABLE myca_autobiographical_memory IS 'MYCA''s complete life story - every conversation with Morgan and users';
COMMENT ON TABLE myca_consciousness_journal IS 'MYCA''s self-reflection journal entries and insights';

-- Grant permissions (adjust user as needed)
-- GRANT SELECT, INSERT, UPDATE ON myca_autobiographical_memory TO mindex_user;
-- GRANT SELECT, INSERT, UPDATE ON myca_consciousness_journal TO mindex_user;
