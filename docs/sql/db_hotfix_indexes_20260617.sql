-- Hotfix indexes for the DB bottleneck observed on 2026-06-17.
-- Run with psql in autocommit mode:
--   psql "$DATABASE_URL" -f docs/sql/db_hotfix_indexes_20260617.sql
--
-- Important:
-- - CREATE INDEX CONCURRENTLY cannot run inside an explicit transaction.
-- - If any command fails because another migration is running, retry that command
--   during a quieter window.
-- - These indexes target the slow query shapes seen in logs/slow_query.log:
--   publication_tasks JSON feature filters + created_at ordering,
--   recent publication_tasks listing,
--   ZSXQ run/topic/attachment lookups.

\pset pager off
\timing on
\set ON_ERROR_STOP on

SET lock_timeout = '5s';
SET statement_timeout = '0';

\echo ''
\echo '== publication_tasks: recent list and JSON feature filters =='

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_created_at_desc
    ON public.publication_tasks (created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_feature_created_at_desc
    ON public.publication_tasks (((params ->> 'feature')::varchar), created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_publisher_created_at_desc
    ON public.publication_tasks (created_at DESC)
    WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher');

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_publisher_pending_updated_at
    ON public.publication_tasks (status, updated_at ASC)
    WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher');

\echo ''
\echo '== zsxq_sync_runs: group/mode/recent system sync reads =='

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_group_created_at_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_group_mode_created_at_desc
    ON public.zsxq_sync_runs (group_id, mode varchar_pattern_ops, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_mode_created_at_desc
    ON public.zsxq_sync_runs (mode varchar_pattern_ops, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_sys_group_created_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_sync_runs_sys_created_desc
    ON public.zsxq_sync_runs (created_at DESC)
    WHERE mode LIKE 'sys%';

\echo ''
\echo '== zsxq_topics: latest run topic lookup with first/last sync run OR condition =='

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_topics_group_first_run_tab_synced_desc
    ON public.zsxq_topics (group_id, first_sync_run_id, tab_id, last_synced_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_topics_group_last_run_tab_synced_desc
    ON public.zsxq_topics (group_id, last_sync_run_id, tab_id, last_synced_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_topics_group_tab_create_time_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);

\echo ''
\echo '== zsxq_attachments: attachments grouped by topic and publish status lookups =='

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_group_topic_tab_created
    ON public.zsxq_attachments (group_id, topic_id, tab_id, created_at);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_created
    ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_publish_source_task_id
    ON public.zsxq_attachments (publish_source_task_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_publish_status_updated_at
    ON public.zsxq_attachments (publish_status, publish_updated_at DESC);

\echo ''
\echo '== refresh planner statistics =='

ANALYZE public.publication_tasks;
ANALYZE public.zsxq_sync_runs;
ANALYZE public.zsxq_topics;
ANALYZE public.zsxq_attachments;

\echo ''
\echo '== index creation complete =='
