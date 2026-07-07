SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- Hot path seen in slow_query.log:
--   SELECT ... FROM zsxq_topics
--   WHERE group_id = ? AND tab_id = ?
--   ORDER BY create_time DESC, last_synced_at DESC
--   LIMIT ?;
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_create_synced_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);

-- Same endpoint also supports all-tabs local browsing by group_id only.
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_create_synced_desc
    ON public.zsxq_topics (group_id, create_time DESC, last_synced_at DESC);

ANALYZE public.zsxq_topics;

-- Verification: expected local-browse indexes.
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'zsxq_topics'
  AND indexname IN (
      'ix_zsxq_topics_group_tab_create_synced_desc',
      'ix_zsxq_topics_group_create_synced_desc'
  )
ORDER BY indexname;

-- Verification: table health.
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
  AND relname = 'zsxq_topics';
