-- Search schema for answer/QA/worldview persistence (Doable Search Rollout).
-- Run once on MINDEX PostgreSQL. Created: March 14, 2026

CREATE SCHEMA IF NOT EXISTS search;

-- Normalized query ledger (for analytics and second-search lookup)
CREATE TABLE IF NOT EXISTS search.query (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_query TEXT NOT NULL,
    query_hash TEXT,
    session_id TEXT,
    user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_search_query_hash ON search.query(query_hash);
CREATE INDEX IF NOT EXISTS idx_search_query_created ON search.query(created_at DESC);

-- Short displayable answers with provenance (instant second-search)
CREATE TABLE IF NOT EXISTS search.answer_snippet (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id UUID REFERENCES search.query(id) ON DELETE SET NULL,
    snippet_text TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'orchestrator',
    source_id TEXT,
    provenance JSONB DEFAULT '{}',
    result_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    freshness_until TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_search_answer_snippet_query ON search.answer_snippet(query_id);
CREATE INDEX IF NOT EXISTS idx_search_answer_snippet_result_hash ON search.answer_snippet(result_hash);
CREATE INDEX IF NOT EXISTS idx_search_answer_snippet_created ON search.answer_snippet(created_at DESC);

-- Reusable Q&A pairs
CREATE TABLE IF NOT EXISTS search.qa_pair (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_normalized TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    source_type TEXT DEFAULT 'orchestrator',
    source_id TEXT,
    provenance JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_search_qa_question ON search.qa_pair(question_normalized);

-- Worldview facts (distilled world facts linked to provenance and freshness)
CREATE TABLE IF NOT EXISTS search.worldview_fact (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_text TEXT NOT NULL,
    category TEXT,
    source_type TEXT DEFAULT 'orchestrator',
    source_id TEXT,
    provenance JSONB DEFAULT '{}',
    freshness_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_search_worldview_category ON search.worldview_fact(category);
CREATE INDEX IF NOT EXISTS idx_search_worldview_created ON search.worldview_fact(created_at DESC);

-- Link answers to investigation artifacts, grounding, research, specialist outputs
CREATE TABLE IF NOT EXISTS search.answer_source (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_snippet_id UUID REFERENCES search.answer_snippet(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_search_answer_source_snippet ON search.answer_source(answer_snippet_id);
