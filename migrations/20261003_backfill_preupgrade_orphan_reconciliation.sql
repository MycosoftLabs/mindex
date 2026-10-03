-- Recover terminal versionless artifacts created before purge_object covered the
-- crash-after-upload-before-reference gap. Never fabricate deletion proof/times.
BEGIN;
INSERT INTO retention.outbox (artifact_id, job_id, event)
SELECT a.artifact_id, j.job_id, 'purge_object'
FROM retention.artifact a
JOIN retention.job j USING (artifact_id)
WHERE a.state IN ('deleted', 'cancelled')
  AND j.state IN ('deleted', 'cancelled')
  AND a.object_version IS NULL
  AND a.deletion_requested_at IS NOT NULL
  AND a.physical_deleted_at IS NULL
  AND a.archive_reconciled_at IS NULL
ORDER BY a.received_at, a.artifact_id
ON CONFLICT (artifact_id, event) DO NOTHING;
COMMIT;
