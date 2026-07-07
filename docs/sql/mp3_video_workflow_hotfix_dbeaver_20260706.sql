-- MP3 video workflow hotfix for the orchestration result page.
-- Run in DBeaver as postgres/admin. Keep Auto-commit enabled.
-- If DBeaver batches statements in a way that holds locks, execute each
-- CREATE INDEX statement one by one during a quiet window.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

GRANT CREATE ON SCHEMA public TO cappa_rw;

SET ROLE cappa_rw;

-- Backfill lightweight projection columns for existing MP3 workflow tasks.
-- Full request/result JSON remains intact; this only makes hot list/stat paths
-- indexable and avoids scanning params/result on every page load.
UPDATE public.publication_tasks
SET
    feature = 'mp3_video_workflow',
    user_id = LEFT(NULLIF(COALESCE(NULLIF(user_id, ''), params ->> 'user_id'), ''), 64),
    source_feature = COALESCE(NULLIF(source_feature, ''), 'zsxq_attachment'),
    source_task_id = LEFT(NULLIF(COALESCE(
        NULLIF(source_task_id, ''),
        params #>> '{mp3_video_workflow,audio_asset_id}',
        params #>> '{mp3_video_workflow,audio,attachment_id}'
    ), ''), 36),
    task_title = LEFT(NULLIF(COALESCE(
        NULLIF(task_title, ''),
        params #>> '{mp3_video_workflow,metadata,title}',
        params #>> '{mp3_video_workflow,audio,topic_title}',
        params #>> '{mp3_video_workflow,audio,filename}',
        result #>> '{plan,title}'
    ), ''), 500)
WHERE COALESCE(NULLIF(feature, ''), params ->> 'feature') = 'mp3_video_workflow'
  AND (
      NULLIF(source_task_id, '') IS NULL
      OR NULLIF(task_title, '') IS NULL
      OR NULLIF(user_id, '') IS NULL
      OR NULLIF(source_feature, '') IS NULL
  );

-- Hot path: /api/mp3-video-workflows/jobs
-- WHERE feature='mp3_video_workflow' AND user_id IN (...)
-- ORDER BY created_at DESC LIMIT/OFFSET
CREATE INDEX IF NOT EXISTS ix_publication_tasks_mp3_user_created_desc
    ON public.publication_tasks (user_id, created_at DESC)
    WHERE feature = 'mp3_video_workflow';

-- Hot path with status filter.
CREATE INDEX IF NOT EXISTS ix_publication_tasks_mp3_user_status_created_desc
    ON public.publication_tasks (user_id, status, created_at DESC)
    WHERE feature = 'mp3_video_workflow';

-- Hot path: /api/mp3-video-workflows/audio-assets/workflow-stats
-- WHERE feature='mp3_video_workflow' AND source_task_id IN (...)
CREATE INDEX IF NOT EXISTS ix_publication_tasks_mp3_source_user_created_desc
    ON public.publication_tasks (source_task_id, user_id, created_at DESC)
    WHERE feature = 'mp3_video_workflow' AND source_task_id IS NOT NULL;

ANALYZE public.publication_tasks;

RESET ROLE;

-- Verification: projection coverage for MP3 workflow rows.
SELECT
    COUNT(*) AS total_mp3_tasks,
    COUNT(*) FILTER (WHERE NULLIF(source_task_id, '') IS NOT NULL) AS with_source_task_id,
    COUNT(*) FILTER (WHERE NULLIF(task_title, '') IS NOT NULL) AS with_task_title,
    COUNT(*) FILTER (WHERE NULLIF(user_id, '') IS NOT NULL) AS with_user_id
FROM public.publication_tasks
WHERE feature = 'mp3_video_workflow';

-- Verification: expected indexes.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'publication_tasks'
  AND indexname IN (
      'ix_publication_tasks_mp3_user_created_desc',
      'ix_publication_tasks_mp3_user_status_created_desc',
      'ix_publication_tasks_mp3_source_user_created_desc'
  )
ORDER BY indexname;
