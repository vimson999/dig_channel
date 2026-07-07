-- Hot indexes from 2026-06-30 10:00 slow-query review.
-- DBeaver-friendly: do not wrap in an explicit transaction and do not use
-- CREATE INDEX CONCURRENTLY, because some DBeaver drivers execute scripts in
-- pipeline/transaction mode.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- publication_tasks is owned by cappa_rw in production.
-- These indexes target report-video Doubao child lookups:
--   WHERE feature = ? AND parent_feature = ? AND parent_task_id = ?
--   ORDER BY created_at ASC/DESC
GRANT CREATE ON SCHEMA public TO cappa_rw;
SET ROLE cappa_rw;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_parent_created_at
    ON public.publication_tasks (feature, parent_feature, parent_task_id, created_at)
    WHERE parent_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_parent_status_created_at
    ON public.publication_tasks (feature, parent_feature, parent_task_id, status, created_at DESC)
    WHERE parent_task_id IS NOT NULL;

ANALYZE public.publication_tasks;

RESET ROLE;

-- Local ZSXQ topic tab list:
--   WHERE group_id = ? GROUP BY tab_id, tab_label ORDER BY max(last_synced_at)
CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_last_synced
    ON public.zsxq_topics (group_id, tab_id, last_synced_at DESC);

-- External document links for topic pages:
--   WHERE group_id = ? AND topic_id IN (...)
CREATE INDEX IF NOT EXISTS ix_zsxq_topic_external_documents_group_topic
    ON public.zsxq_topic_external_documents (group_id, topic_id, tab_id);

ANALYZE public.zsxq_topics;
ANALYZE public.zsxq_topic_external_documents;

-- Verification.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_publication_tasks_feature_parent_created_at',
      'ix_publication_tasks_feature_parent_status_created_at',
      'ix_zsxq_topics_group_tab_last_synced',
      'ix_zsxq_topic_external_documents_group_topic'
  )
ORDER BY tablename, indexname;

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
      'zsxq_topics',
      'zsxq_topic_external_documents'
  )
ORDER BY relname;
