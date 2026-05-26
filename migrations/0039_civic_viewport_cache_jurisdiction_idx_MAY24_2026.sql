-- Faster jurisdiction-key cache lookups for viewport intel (May 24, 2026)

BEGIN;

CREATE INDEX IF NOT EXISTS idx_civic_viewport_cache_jurisdiction_lod
  ON civic.viewport_cache (jurisdiction_key, lod, expires_at DESC);

COMMIT;
