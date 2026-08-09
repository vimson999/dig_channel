-- Candidate cleanup for an unused attachment file-id composite index.
-- HUMAN-RUN ONLY. Execute one section at a time in DBeaver as the index owner.
-- Keep auto-commit enabled: DROP/CREATE INDEX CONCURRENTLY cannot run in a
-- transaction block. Do not batch this file.

-- 1) Preflight. Abort if idx_scan is no longer 0, the definition differs, or
-- statistics were reset after 2026-07-17 without a full business cycle.
SELECT
    now() AS checked_at,
    pg_postmaster_start_time() AS server_started_at,
    stats_reset,
    s.relname AS table_name,
    s.indexrelname AS index_name,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
    s.idx_scan,
    s.idx_tup_read,
    s.idx_tup_fetch,
    pg_get_indexdef(s.indexrelid) AS index_definition
FROM pg_stat_user_indexes AS s
LEFT JOIN pg_stat_database AS d
  ON d.datname = current_database()
WHERE s.schemaname = 'public'
  AND s.indexrelname = 'ix_zsxq_attachments_group_tab_status_file';

SELECT
    i.indisvalid,
    i.indisready,
    i.indislive,
    pg_get_indexdef(i.indexrelid) AS index_definition
FROM pg_index AS i
WHERE i.indexrelid =
      'public.ix_zsxq_attachments_group_tab_status_file'::regclass;

-- 2) Evidence query. Supply values copied from a real attachment-status
-- request. The 2026-08-01 production sample used the sibling
-- group_tab_status_key index, filtered 5,368 rows, and executed in 4.182 ms;
-- it did not use this file-id index. Save the plan before continuing.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT
    id,
    group_id,
    tab_id,
    topic_id,
    attachment_key,
    name,
    file_id,
    download_status,
    updated_at
FROM public.zsxq_attachments
WHERE group_id = :group_id
  AND tab_id = :tab_id
  AND download_status IN ('downloaded', 'failed')
  AND (
      file_id IN (:file_id)
      OR attachment_key IN (:attachment_key)
      OR name IN (:name)
  );

-- 3) Change. Run only after sections 1-2 are reviewed. Expected lock risk is
-- low because CONCURRENTLY avoids a long ACCESS EXCLUSIVE table lock; the
-- 5-second lock timeout prevents waiting behind an unsafe lock convoy.
SET lock_timeout = '5s';
SET statement_timeout = '10min';
DROP INDEX CONCURRENTLY IF EXISTS
    public.ix_zsxq_attachments_group_tab_status_file;

-- 4) Verification. The first query should return 0 rows. Re-run the real
-- EXPLAIN from section 2 and compare execution time, buffers, and row count.
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname = 'ix_zsxq_attachments_group_tab_status_file';

SELECT
    relname,
    n_live_tup,
    n_dead_tup,
    seq_scan,
    idx_scan,
    n_tup_upd,
    n_tup_hot_upd
FROM pg_stat_user_tables
WHERE schemaname = 'public'
  AND relname = 'zsxq_attachments';

-- 5) Rollback. Use if the real attachment-status plan or latency regresses.
-- Keep auto-commit enabled and execute this statement by itself.
SET lock_timeout = '5s';
SET statement_timeout = '10min';
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    ix_zsxq_attachments_group_tab_status_file
ON public.zsxq_attachments
    (group_id, tab_id, download_status, file_id);

ANALYZE public.zsxq_attachments;
