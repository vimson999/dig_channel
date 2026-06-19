-- Read-only PostgreSQL bottleneck diagnostics for the current DB issue.
-- Run with psql:
--   psql "$DATABASE_URL" -f docs/sql/db_bottleneck_diagnostics_20260617.sql
--
-- Notes:
-- - This file is intended for psql because it uses \echo, \gset and \if.
-- - EXPLAIN ANALYZE executes the SELECT statements. Run during a quiet window
--   if production load is already high.

\pset pager off
\timing on
\set ON_ERROR_STOP on
\set zsxq_group_id 28888112822211

SET statement_timeout = '60s';

\echo ''
\echo '== 1. Database identity =='
SELECT
    now() AS checked_at,
    current_database() AS database_name,
    current_user AS current_user,
    version() AS postgres_version;

\echo ''
\echo '== 2. Target table sizes =='
WITH target_tables(relname) AS (
    VALUES
        ('public.publication_tasks'),
        ('public.zsxq_sync_runs'),
        ('public.zsxq_topics'),
        ('public.zsxq_attachments')
)
SELECT
    relname,
    pg_size_pretty(pg_relation_size(to_regclass(relname))) AS table_size,
    pg_size_pretty(pg_indexes_size(to_regclass(relname))) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(to_regclass(relname))) AS total_size
FROM target_tables
WHERE to_regclass(relname) IS NOT NULL
ORDER BY pg_total_relation_size(to_regclass(relname)) DESC;

\echo ''
\echo '== 3. Existing indexes on hot tables =='
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN (
      'publication_tasks',
      'zsxq_sync_runs',
      'zsxq_topics',
      'zsxq_attachments'
  )
ORDER BY tablename, indexname;

\echo ''
\echo '== 4. Table scan, vacuum and analyze stats =='
SELECT
    relname,
    n_live_tup,
    n_dead_tup,
    seq_scan,
    idx_scan,
    CASE
        WHEN (seq_scan + idx_scan) = 0 THEN NULL
        ELSE round((idx_scan::numeric / (seq_scan + idx_scan)) * 100, 2)
    END AS idx_scan_pct,
    last_vacuum,
    last_autovacuum,
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
ORDER BY n_dead_tup DESC;

\echo ''
\echo '== 5. Current activity summary =='
SELECT
    state,
    wait_event_type,
    wait_event,
    count(*) AS sessions,
    max(clock_timestamp() - COALESCE(query_start, state_change)) AS max_age,
    left(max(query), 180) AS sample_query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
GROUP BY state, wait_event_type, wait_event
ORDER BY sessions DESC, max_age DESC NULLS LAST;

\echo ''
\echo '== 6. Oldest active or waiting sessions =='
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    wait_event_type,
    wait_event,
    clock_timestamp() - COALESCE(query_start, state_change) AS age,
    left(query, 300) AS query_sample
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY COALESCE(query_start, state_change) ASC NULLS LAST
LIMIT 20;

\echo ''
\echo '== 7. Blocking locks, if any =='
SELECT
    blocked.pid AS blocked_pid,
    blocked_activity.usename AS blocked_user,
    blocking.pid AS blocking_pid,
    blocking_activity.usename AS blocking_user,
    blocked_activity.wait_event_type,
    blocked_activity.wait_event,
    now() - blocked_activity.query_start AS blocked_age,
    left(blocked_activity.query, 240) AS blocked_query,
    left(blocking_activity.query, 240) AS blocking_query
FROM pg_locks blocked
JOIN pg_stat_activity blocked_activity
  ON blocked_activity.pid = blocked.pid
JOIN pg_locks blocking
  ON blocking.locktype = blocked.locktype
 AND blocking.database IS NOT DISTINCT FROM blocked.database
 AND blocking.relation IS NOT DISTINCT FROM blocked.relation
 AND blocking.page IS NOT DISTINCT FROM blocked.page
 AND blocking.tuple IS NOT DISTINCT FROM blocked.tuple
 AND blocking.virtualxid IS NOT DISTINCT FROM blocked.virtualxid
 AND blocking.transactionid IS NOT DISTINCT FROM blocked.transactionid
 AND blocking.classid IS NOT DISTINCT FROM blocked.classid
 AND blocking.objid IS NOT DISTINCT FROM blocked.objid
 AND blocking.objsubid IS NOT DISTINCT FROM blocked.objsubid
 AND blocking.pid <> blocked.pid
JOIN pg_stat_activity blocking_activity
  ON blocking_activity.pid = blocking.pid
WHERE NOT blocked.granted
  AND blocking.granted
ORDER BY blocked_age DESC;

\echo ''
\echo '== 8. pg_stat_statements top queries, if extension is installed =='
SELECT CASE
    WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')
    THEN 'true'
    ELSE 'false'
END AS pgss_installed
\gset

\if :pgss_installed
SELECT
    calls,
    round(mean_exec_time::numeric, 2) AS mean_ms,
    round(max_exec_time::numeric, 2) AS max_ms,
    round(total_exec_time::numeric, 2) AS total_ms,
    rows,
    left(query, 500) AS query_sample
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND (
      query ILIKE '%publication_tasks%'
      OR query ILIKE '%zsxq_sync_runs%'
      OR query ILIKE '%zsxq_topics%'
      OR query ILIKE '%zsxq_attachments%'
  )
ORDER BY total_exec_time DESC
LIMIT 20;
\else
\echo 'pg_stat_statements is not installed; skipping historical query stats.'
\endif

\echo ''
\echo '== 9. EXPLAIN current slow query: publisher task list with JSON filter =='
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    id,
    celery_task_id,
    status,
    current_step,
    progress,
    params,
    result,
    error_message,
    created_at,
    updated_at,
    completed_at
FROM public.publication_tasks
WHERE ((params ->> 'feature')::varchar IS NULL OR (params ->> 'feature')::varchar = 'publisher')
ORDER BY created_at DESC
LIMIT 20;

\echo ''
\echo '== 10. EXPLAIN current slow query: content pipeline task list =='
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    id,
    celery_task_id,
    status,
    current_step,
    progress,
    params,
    result,
    error_message,
    created_at,
    updated_at,
    completed_at
FROM public.publication_tasks
WHERE (params ->> 'feature')::varchar IN ('content_pdf_clean', 'content_audio_clean')
ORDER BY created_at DESC
LIMIT 20;

\echo ''
\echo '== 11. EXPLAIN current slow query: full recent publication tasks =='
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    id,
    status,
    params,
    result,
    created_at,
    updated_at,
    completed_at
FROM public.publication_tasks
ORDER BY created_at DESC
LIMIT 20;

\echo ''
\echo '== 12. EXPLAIN lean version: recent publication tasks without large JSON columns =='
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    id,
    celery_task_id,
    status,
    current_step,
    progress,
    created_at,
    updated_at,
    completed_at
FROM public.publication_tasks
ORDER BY created_at DESC
LIMIT 20;

\echo ''
\echo '== 13. EXPLAIN ZSXQ system sync runs =='
EXPLAIN (ANALYZE, BUFFERS)
SELECT
    id,
    group_id,
    mode,
    status,
    created_at,
    completed_at,
    result
FROM public.zsxq_sync_runs
WHERE group_id = :'zsxq_group_id'
  AND mode LIKE 'sys%'
ORDER BY created_at DESC
LIMIT 120;

\echo ''
\echo '== 14. EXPLAIN ZSXQ topics for latest run sample =='
EXPLAIN (ANALYZE, BUFFERS)
WITH latest_run AS (
    SELECT id
    FROM public.zsxq_sync_runs
    WHERE group_id = :'zsxq_group_id'
    ORDER BY created_at DESC
    LIMIT 1
),
sample_tabs AS (
    SELECT DISTINCT tab_id
    FROM public.zsxq_topics
    WHERE group_id = :'zsxq_group_id'
    ORDER BY tab_id
    LIMIT 2
)
SELECT
    id,
    group_id,
    tab_id,
    topic_id,
    title,
    first_sync_run_id,
    last_sync_run_id,
    last_synced_at
FROM public.zsxq_topics
WHERE group_id = :'zsxq_group_id'
  AND (
      first_sync_run_id = (SELECT id FROM latest_run)
      OR last_sync_run_id = (SELECT id FROM latest_run)
  )
  AND tab_id IN (SELECT tab_id FROM sample_tabs)
ORDER BY last_synced_at DESC
LIMIT 81;

\echo ''
\echo '== 15. EXPLAIN ZSXQ attachments by topic sample =='
EXPLAIN (ANALYZE, BUFFERS)
WITH sample_topics AS (
    SELECT tab_id, topic_id
    FROM public.zsxq_topics
    WHERE group_id = :'zsxq_group_id'
    ORDER BY last_synced_at DESC
    LIMIT 80
)
SELECT
    id,
    group_id,
    tab_id,
    topic_id,
    attachment_key,
    name,
    publish_status,
    publish_task_id,
    created_at
FROM public.zsxq_attachments
WHERE group_id = :'zsxq_group_id'
  AND topic_id IN (SELECT topic_id FROM sample_topics)
  AND tab_id IN (SELECT tab_id FROM sample_topics)
ORDER BY topic_id ASC, created_at ASC;
