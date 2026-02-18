-- Voice Session Store Migration - February 12, 2026
-- Creates tables for PersonaPlex voice session persistence

-- Ensure memory schema exists
CREATE SCHEMA IF NOT EXISTS memory;

-- =============================================================================
-- voice_sessions - Main session tracking table
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory.voice_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL,
    mode VARCHAR(32) DEFAULT 'personaplex',
    persona VARCHAR(32) DEFAULT 'myca',
    user_id UUID,
    voice_prompt VARCHAR(128),
    metadata JSONB DEFAULT '{}'::jsonb,
    turn_count INTEGER DEFAULT 0,
    tool_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    summary TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_voice_sessions_conversation 
    ON memory.voice_sessions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_voice_sessions_user 
    ON memory.voice_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_voice_sessions_active 
    ON memory.voice_sessions(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_voice_sessions_started 
    ON memory.voice_sessions(started_at DESC);

-- =============================================================================
-- voice_turns - Individual conversation turns
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory.voice_turns (
    turn_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) REFERENCES memory.voice_sessions(session_id) ON DELETE CASCADE,
    speaker VARCHAR(32) NOT NULL,  -- 'user', 'myca', 'system'
    text TEXT NOT NULL,
    duration_ms INTEGER,
    latency_ms INTEGER,
    confidence FLOAT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_voice_turns_session 
    ON memory.voice_turns(session_id);
CREATE INDEX IF NOT EXISTS idx_voice_turns_created 
    ON memory.voice_turns(created_at);

-- =============================================================================
-- voice_tool_invocations - Tool call logging
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory.voice_tool_invocations (
    invocation_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) REFERENCES memory.voice_sessions(session_id) ON DELETE CASCADE,
    turn_id VARCHAR(64),
    agent VARCHAR(64) NOT NULL,
    action VARCHAR(128) NOT NULL,
    status VARCHAR(32) DEFAULT 'pending',  -- 'pending', 'success', 'error', 'cancelled'
    input_params JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    latency_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_voice_tool_session 
    ON memory.voice_tool_invocations(session_id);
CREATE INDEX IF NOT EXISTS idx_voice_tool_status 
    ON memory.voice_tool_invocations(status);

-- =============================================================================
-- voice_barge_in_events - User interruption tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory.voice_barge_in_events (
    event_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) REFERENCES memory.voice_sessions(session_id) ON DELETE CASCADE,
    cancelled_text TEXT,
    cancelled_at_position_ms INTEGER,
    user_intent VARCHAR(128),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_voice_barge_session 
    ON memory.voice_barge_in_events(session_id);

-- =============================================================================
-- Stored Procedures
-- =============================================================================

-- end_voice_session - Mark session as ended with optional summary
CREATE OR REPLACE FUNCTION memory.end_voice_session(
    p_session_id VARCHAR(64),
    p_summary TEXT DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    UPDATE memory.voice_sessions 
    SET 
        is_active = FALSE,
        ended_at = NOW(),
        summary = COALESCE(p_summary, summary),
        updated_at = NOW()
    WHERE session_id = p_session_id;
END;
$$ LANGUAGE plpgsql;

-- get_session_with_turns - Get session with all turns as JSON
CREATE OR REPLACE FUNCTION memory.get_session_with_turns(
    p_session_id VARCHAR(64)
) RETURNS TABLE (
    session_id VARCHAR(64),
    conversation_id VARCHAR(64),
    mode VARCHAR(32),
    persona VARCHAR(32),
    user_id UUID,
    turn_count INTEGER,
    tool_count INTEGER,
    is_active BOOLEAN,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    summary TEXT,
    turns JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        s.session_id,
        s.conversation_id,
        s.mode,
        s.persona,
        s.user_id,
        s.turn_count,
        s.tool_count,
        s.is_active,
        s.started_at,
        s.ended_at,
        s.summary,
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'turn_id', t.turn_id,
                    'speaker', t.speaker,
                    'text', t.text,
                    'duration_ms', t.duration_ms,
                    'created_at', t.created_at
                ) ORDER BY t.created_at
            ) FILTER (WHERE t.turn_id IS NOT NULL),
            '[]'::jsonb
        ) as turns
    FROM memory.voice_sessions s
    LEFT JOIN memory.voice_turns t ON s.session_id = t.session_id
    WHERE s.session_id = p_session_id
    GROUP BY s.session_id, s.conversation_id, s.mode, s.persona, s.user_id, 
             s.turn_count, s.tool_count, s.is_active, s.started_at, s.ended_at, s.summary;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Statistics View
-- =============================================================================
CREATE OR REPLACE VIEW memory.voice_session_stats AS
SELECT 
    COUNT(*) as total_sessions,
    COUNT(*) FILTER (WHERE is_active = TRUE) as active_sessions,
    COUNT(*) FILTER (WHERE ended_at IS NOT NULL) as completed_sessions,
    SUM(turn_count) as total_turns,
    SUM(tool_count) as total_tool_invocations,
    AVG(turn_count) as avg_turns_per_session,
    AVG(EXTRACT(EPOCH FROM (ended_at - started_at))) FILTER (WHERE ended_at IS NOT NULL) as avg_session_duration_seconds,
    MAX(started_at) as last_session_started,
    COUNT(DISTINCT conversation_id) as unique_conversations,
    COUNT(DISTINCT user_id) as unique_users
FROM memory.voice_sessions;

-- =============================================================================
-- Grant permissions (adjust as needed for your setup)
-- =============================================================================
GRANT USAGE ON SCHEMA memory TO mycosoft;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA memory TO mycosoft;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA memory TO mycosoft;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO mycosoft;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'Voice Session Store migration completed successfully';
END $$;
