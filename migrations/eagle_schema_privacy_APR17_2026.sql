-- Eagle Eye — provenance / privacy columns (Apr 17, 2026)
-- Apply after eagle_schema_APR20_2026.sql

ALTER TABLE eagle.video_sources
    ADD COLUMN IF NOT EXISTS provenance_method TEXT,
    ADD COLUMN IF NOT EXISTS privacy_class TEXT;

COMMENT ON COLUMN eagle.video_sources.provenance_method IS
    'official_api | iframe | partner | quarantined_scrape | direct_stream';
COMMENT ON COLUMN eagle.video_sources.privacy_class IS
    'public_embed | public_exact_geo | restricted | operator_only';

ALTER TABLE eagle.video_events
    ADD COLUMN IF NOT EXISTS exact_geo_allowed BOOLEAN DEFAULT true;

COMMENT ON COLUMN eagle.video_events.exact_geo_allowed IS
    'When false, map must not show precise coordinates for this clip.';
