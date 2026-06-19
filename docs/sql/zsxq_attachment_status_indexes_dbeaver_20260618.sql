-- DBeaver-friendly hot indexes for ZSXQ attachment/status/read paths.
-- Run as the owner of public.zsxq_* tables or a database admin role.
-- This script intentionally avoids CREATE INDEX CONCURRENTLY because
-- DBeaver/JDBC may execute statements in a pipeline/batch mode.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- Main visible-topic attachment status lookup:
-- WHERE group_id = ? AND tab_id = ? AND topic_id IN (...)
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_created
    ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at);

-- Fallback lookup for already terminal attachment statuses across tabs:
-- WHERE group_id = ? AND topic_id IN (...) AND download_status IN (...)
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_topic_status
    ON public.zsxq_attachments (group_id, topic_id, download_status);

-- Attachment-ref lookup by exact file id / attachment key inside a tab:
-- WHERE group_id = ? AND tab_id = ? AND download_status IN (...) AND file_id IN (...)
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_status_file
    ON public.zsxq_attachments (group_id, tab_id, download_status, file_id);

-- WHERE group_id = ? AND tab_id = ? AND download_status IN (...) AND attachment_key IN (...)
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_status_key
    ON public.zsxq_attachments (group_id, tab_id, download_status, attachment_key);

-- Stale sync-run recovery:
-- WHERE group_id = ? AND status = 'running' AND updated_at < ?
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_status_updated
    ON public.zsxq_sync_runs (group_id, status, updated_at);

-- Same recovery path when no group filter is supplied.
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_status_updated
    ON public.zsxq_sync_runs (status, updated_at);

ANALYZE public.zsxq_attachments;
ANALYZE public.zsxq_sync_runs;

-- Verification: expected indexes.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_zsxq_attachments_group_tab_topic_created',
      'ix_zsxq_attachments_group_topic_status',
      'ix_zsxq_attachments_group_tab_status_file',
      'ix_zsxq_attachments_group_tab_status_key',
      'ix_zsxq_sync_runs_group_status_updated',
      'ix_zsxq_sync_runs_status_updated'
  )
ORDER BY tablename, indexname;

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
  AND relname IN ('zsxq_attachments', 'zsxq_sync_runs')
ORDER BY relname;
