-- Follow-up indexes from slow_query.log on 2026-07-14.
-- Run in psql/DBeaver as postgres/admin. Keep Auto-commit enabled.
-- Execute each CREATE INDEX CONCURRENTLY as an individual statement if your client batches scripts.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_report_audio_user_created_desc
    ON public.publication_tasks (user_id, created_at DESC)
    WHERE feature = 'report_pdf_audio_workflow';

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_report_audio_pdf_user_created_desc
    ON public.publication_tasks (report_audio_pdf_attachment_id, user_id, created_at DESC)
    WHERE feature = 'report_pdf_audio_workflow'
      AND report_audio_pdf_attachment_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_publisher_active_parent_cleanup
    ON public.publication_tasks (updated_at ASC, created_at ASC)
    WHERE feature = 'publisher'
      AND status IN ('pending', 'processing')
      AND (parent_task_id IS NOT NULL OR source_task_id IS NOT NULL);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publication_tasks_publisher_stale_pending_repair
    ON public.publication_tasks (updated_at ASC, created_at ASC)
    WHERE feature = 'publisher'
      AND status = 'pending'
      AND current_step IN (
          '',
          'init',
          'queued',
          'deferred_capacity',
          'deferred_backlog',
          'deferred_db_pressure'
      );

-- Requires postgres/table owner because zsxq_attachments is owned by postgres.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zsxq_attachments_downloaded_pdf_updated_desc
    ON public.zsxq_attachments (updated_at DESC, created_at DESC)
    WHERE download_status = 'downloaded'
      AND local_path IS NOT NULL
      AND file_ext = 'pdf';

ANALYZE public.publication_tasks;
ANALYZE public.zsxq_attachments;

SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_publication_tasks_report_audio_user_created_desc',
      'ix_publication_tasks_report_audio_pdf_user_created_desc',
      'ix_publication_tasks_publisher_active_parent_cleanup',
      'ix_publication_tasks_publisher_stale_pending_repair',
      'ix_zsxq_attachments_downloaded_pdf_updated_desc'
  )
ORDER BY tablename, indexname;
