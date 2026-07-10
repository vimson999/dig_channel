-- PublicationTask relationship hot indexes.
-- Safe to rerun in DBeaver or psql.
--
-- Hot paths:
--   1. Report-audio list/detail child summaries by parent_task_id/source_task_id.
--   2. Publisher vertical child lookup by feature + source_task_id.
--   3. Material workflow stats by feature + source_task_id / pdf attachment id.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

CREATE INDEX IF NOT EXISTS ix_publication_tasks_parent_created_asc
    ON public.publication_tasks (parent_task_id, created_at ASC)
    WHERE parent_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_source_created_asc
    ON public.publication_tasks (source_task_id, created_at ASC)
    WHERE source_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_source_created_desc
    ON public.publication_tasks (feature, source_task_id, created_at DESC)
    WHERE source_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_pdf_attachment_created_desc
    ON public.publication_tasks (feature, report_audio_pdf_attachment_id, created_at DESC)
    WHERE report_audio_pdf_attachment_id IS NOT NULL;

ANALYZE public.publication_tasks;

-- Verification: expected relationship indexes.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_publication_tasks_parent_created_asc',
      'ix_publication_tasks_source_created_asc',
      'ix_publication_tasks_feature_source_created_desc',
      'ix_publication_tasks_feature_pdf_attachment_created_desc'
  )
ORDER BY tablename, indexname;

-- Verification: rough relationship cardinality.
SELECT
    count(*) FILTER (WHERE parent_task_id IS NOT NULL) AS rows_with_parent_task_id,
    count(*) FILTER (WHERE source_task_id IS NOT NULL) AS rows_with_source_task_id,
    count(*) FILTER (WHERE report_audio_pdf_attachment_id IS NOT NULL) AS rows_with_pdf_attachment_id,
    count(*) AS total_rows
FROM public.publication_tasks;
