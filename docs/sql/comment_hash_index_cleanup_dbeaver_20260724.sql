-- ZSXQ comment write-amplification and redundant-index cleanup.
-- Run as postgres/admin. This script is intentionally non-concurrent so it can
-- be executed as one DBeaver script without the "pipeline" error.
-- Stop backend/Celery/schedulers first. Auto-commit may be on or off.

SET lock_timeout = '30s';
SET statement_timeout = '10min';

ALTER TABLE public.zsxq_topic_comments
    ADD COLUMN IF NOT EXISTS raw_payload_hash varchar(32);

UPDATE public.zsxq_topic_comments
SET raw_payload_hash = md5(raw_payload::jsonb::text)
WHERE raw_payload IS NOT NULL
  AND raw_payload_hash IS NULL;

DROP INDEX IF EXISTS public.ix_external_documents_url_hash;
DROP INDEX IF EXISTS public.ix_publication_tasks_feature_status_updated;
DROP INDEX IF EXISTS public.ix_publication_tasks_feature_created_at_desc;
DROP INDEX IF EXISTS public.ix_publication_tasks_publisher_created_at_desc;
DROP INDEX IF EXISTS public.ix_publication_tasks_publisher_pending_updated_at;
DROP INDEX IF EXISTS public.ix_zsxq_topics_group_tab_type_create_desc;
DROP INDEX IF EXISTS public.ix_zsxq_topics_group_type_create_desc;
DROP INDEX IF EXISTS public.ix_zsxq_attachments_publish_status;
DROP INDEX IF EXISTS public.ix_zsxq_topic_comments_tab_id;
DROP INDEX IF EXISTS public.ix_zsxq_topic_comments_parent_comment_id;
DROP INDEX IF EXISTS public.ix_zsxq_topic_comments_owner_user_id;
DROP INDEX IF EXISTS public.ix_zsxq_topic_comments_owner_name;
DROP INDEX IF EXISTS public.ix_zsxq_topic_comments_create_time;

ANALYZE public.zsxq_topic_comments;
ANALYZE public.zsxq_topics;
ANALYZE public.zsxq_attachments;
ANALYZE public.publication_tasks;
ANALYZE public.external_documents;

UPDATE public.alembic_version
SET version_num = '20260724_comment_hash_indexes'
WHERE version_num = '20260722_zsxq_topic_payload_hash';

SELECT
    count(*) AS comment_rows,
    count(*) FILTER (WHERE raw_payload_hash IS NULL) AS missing_payload_hash
FROM public.zsxq_topic_comments;

SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN (
      'publication_tasks',
      'zsxq_topics',
      'zsxq_topic_comments',
      'zsxq_attachments',
      'external_documents'
  )
ORDER BY tablename, indexname;

SELECT version_num
FROM public.alembic_version;
