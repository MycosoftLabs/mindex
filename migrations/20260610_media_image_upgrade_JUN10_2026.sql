-- Replace bootstrap media.image stub (4 columns) with full HQ schema when table is empty.
BEGIN;

DO $$
DECLARE
    row_count bigint;
    has_table boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'media' AND table_name = 'image'
    ) INTO has_table;
    IF NOT has_table THEN
        RAISE NOTICE 'media.image absent — proceed with full schema migration';
        RETURN;
    END IF;
    SELECT count(*) INTO row_count FROM media.image;
    IF row_count = 0 THEN
        DROP TABLE media.image CASCADE;
        RAISE NOTICE 'Dropped empty bootstrap media.image — apply full media schema next';
    ELSE
        RAISE EXCEPTION 'media.image has % rows — manual migration required', row_count;
    END IF;
END $$;

COMMIT;
