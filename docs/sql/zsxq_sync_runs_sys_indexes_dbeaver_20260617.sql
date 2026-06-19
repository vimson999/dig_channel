-- Minimal DBeaver script for the remaining ZSXQ system-batch indexes.
-- Run with the postgres/admin connection because zsxq_sync_runs is owned by postgres.
-- This intentionally avoids CREATE INDEX CONCURRENTLY for DBeaver compatibility.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_group_created_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_created_desc
    ON public.zsxq_sync_runs (created_at DESC)
    WHERE mode LIKE 'sys%';

ANALYZE public.zsxq_sync_runs;

SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_zsxq_sync_runs_sys_group_created_desc',
      'ix_zsxq_sync_runs_sys_created_desc'
  )
ORDER BY tablename, indexname;
