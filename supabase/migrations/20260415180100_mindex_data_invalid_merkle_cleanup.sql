-- Supabase public.mindex_data: remove rows that violate validate_merkle_root (truncated / bad hex).
-- Safe for test rows; if any row is real chain data, restore from source with full 0x + 64 hex chars.

DELETE FROM public.mindex_data
WHERE merkle_root IS NOT NULL
  AND (
    length(merkle_root) <> 66
    OR merkle_root !~ '^0x[0-9a-fA-F]{64}$'
  );
