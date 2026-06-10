-- Optional pgvector on VM 189. Drop broken extension metadata if shared library missing.
DO $$
BEGIN
    PERFORM 1 FROM pg_extension WHERE extname = 'vector';
    IF FOUND THEN
        BEGIN
            PERFORM NULL::vector;
        EXCEPTION
            WHEN OTHERS THEN
                DROP EXTENSION IF EXISTS vector;
                RAISE NOTICE 'Dropped broken pgvector extension — using embedding_json on media.image';
        END;
    END IF;
END $$;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
    ALTER TABLE media.image ADD COLUMN IF NOT EXISTS embedding vector(512);
    RAISE NOTICE 'pgvector enabled with media.image.embedding';
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable — embedding_json column remains canonical on VM 189';
END $$;
