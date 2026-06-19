-- DBeaver-friendly DB hotfix indexes for 2026-06-17.
--
-- Why this file does NOT use CREATE INDEX CONCURRENTLY:
-- DBeaver/JDBC may execute multi-statement scripts through a pipeline/batch,
-- while PostgreSQL rejects CREATE INDEX CONCURRENTLY in that mode.
--
-- This script uses regular CREATE INDEX IF NOT EXISTS so you can run the whole
-- file in DBeaver as one SQL script. Based on the 2026-06-17 diagnostics, these
-- tables are small, so the write lock should be brief. If the DB is busy, the
-- 5s lock_timeout will make the script fail instead of waiting indefinitely.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- publication_tasks: recent list and JSON feature filters.
CREATE INDEX IF NOT EXISTS ix_publication_tasks_created_at_desc
    ON public.publication_tasks (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_created_at_desc
    ON public.publication_tasks (((params ->> 'feature')::varchar), created_at DESC);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_created_at_desc
    ON public.publication_tasks (created_at DESC)
    WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher');

CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_pending_updated_at
    ON public.publication_tasks (status, updated_at ASC)
    WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher');

-- zsxq_sync_runs: group/mode/recent system sync reads.
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_created_at_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_mode_created_at_desc
    ON public.zsxq_sync_runs (group_id, mode varchar_pattern_ops, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_mode_created_at_desc
    ON public.zsxq_sync_runs (mode varchar_pattern_ops, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_group_created_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_created_desc
    ON public.zsxq_sync_runs (created_at DESC)
    WHERE mode LIKE 'sys%';

-- zsxq_topics: latest-run topic lookup with first/last sync run OR condition.
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_first_run_tab_synced_desc
    ON public.zsxq_topics (group_id, first_sync_run_id, tab_id, last_synced_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_last_run_tab_synced_desc
    ON public.zsxq_topics (group_id, last_sync_run_id, tab_id, last_synced_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_create_time_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);

-- zsxq_attachments: attachments grouped by topic and publish status lookups.
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_topic_tab_created
    ON public.zsxq_attachments (group_id, topic_id, tab_id, created_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_created
    ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_source_task_id
    ON public.zsxq_attachments (publish_source_task_id);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_publish_status_updated_at
    ON public.zsxq_attachments (publish_status, publish_updated_at DESC);

-- Refresh planner statistics after index creation.
ANALYZE public.publication_tasks;
ANALYZE public.zsxq_sync_runs;
ANALYZE public.zsxq_topics;
ANALYZE public.zsxq_attachments;

-- Verification: indexes expected by this hotfix.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_publication_tasks_created_at_desc',
      'ix_publication_tasks_feature_created_at_desc',
      'ix_publication_tasks_publisher_created_at_desc',
      'ix_publication_tasks_publisher_pending_updated_at',
      'ix_zsxq_sync_runs_group_created_at_desc',
      'ix_zsxq_sync_runs_group_mode_created_at_desc',
      'ix_zsxq_sync_runs_mode_created_at_desc',
      'ix_zsxq_sync_runs_sys_group_created_desc',
      'ix_zsxq_sync_runs_sys_created_desc',
      'ix_zsxq_topics_group_first_run_tab_synced_desc',
      'ix_zsxq_topics_group_last_run_tab_synced_desc',
      'ix_zsxq_topics_group_tab_create_time_desc',
      'ix_zsxq_attachments_group_topic_tab_created',
      'ix_zsxq_attachments_group_tab_topic_created',
      'ix_zsxq_attachments_publish_source_task_id',
      'ix_zsxq_attachments_publish_status_updated_at'
  )
ORDER BY tablename, indexname;

-- Verification: current table health.
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
  AND relname IN (
      'publication_tasks',
      'zsxq_sync_runs',
      'zsxq_topics',
      'zsxq_attachments'
  )
ORDER BY relname;
