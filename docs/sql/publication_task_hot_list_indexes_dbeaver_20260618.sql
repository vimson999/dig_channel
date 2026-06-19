-- DBeaver-friendly hot indexes for publication_tasks list/count/child lookups.
-- Run as postgres/admin. publication_tasks is often owned by cappa_rw, so this
-- script switches role for the index creation section.
-- This script intentionally avoids CREATE INDEX CONCURRENTLY because
-- DBeaver/JDBC may execute statements in a pipeline/batch mode.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

GRANT CREATE ON SCHEMA public TO cappa_rw;

SET ROLE cappa_rw;

-- Report-audio workflow list/count:
-- WHERE feature = ? AND user_id = ?
-- ORDER BY created_at DESC LIMIT/OFFSET
CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_user_created_desc
    ON public.publication_tasks (feature, user_id, created_at DESC);

-- Publisher/all-feature list:
-- WHERE feature = ?
-- ORDER BY created_at DESC LIMIT
CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_created_desc_v2
    ON public.publication_tasks (feature, created_at DESC);

-- Celery monitor business task snapshot:
-- WHERE feature IN (...) AND status IN (...)
-- ORDER BY updated_at DESC, created_at DESC LIMIT
CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_status_updated_created_desc
    ON public.publication_tasks (feature, status, updated_at DESC, created_at DESC);

-- Child task expansion in report-audio workflow list:
-- WHERE parent_task_id IN (...)
-- ORDER BY created_at ASC
CREATE INDEX IF NOT EXISTS ix_publication_tasks_parent_created
    ON public.publication_tasks (parent_task_id, created_at)
    WHERE parent_task_id IS NOT NULL;

-- Linked source task expansion:
-- WHERE source_task_id IN (...)
-- ORDER BY created_at ASC
CREATE INDEX IF NOT EXISTS ix_publication_tasks_source_created
    ON public.publication_tasks (source_task_id, created_at)
    WHERE source_task_id IS NOT NULL;

ANALYZE public.publication_tasks;

RESET ROLE;

-- Verification: expected hot list indexes.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'publication_tasks'
  AND indexname IN (
      'ix_publication_tasks_feature_user_created_desc',
      'ix_publication_tasks_feature_created_desc_v2',
      'ix_publication_tasks_feature_status_updated_created_desc',
      'ix_publication_tasks_parent_created',
      'ix_publication_tasks_source_created'
  )
ORDER BY indexname;

-- Verification: table scan/index-scan health.
SELECT
    relname,
    n_live_tup,
    n_dead_tup,
    seq_scan,
    idx_scan,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname = 'public'
  AND relname = 'publication_tasks';
