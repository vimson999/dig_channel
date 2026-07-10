-- Hot slow-query follow-up for 2026-07-10.
-- DBeaver-safe: no CREATE INDEX CONCURRENTLY and no explicit transaction wrapper.
-- Run as postgres/admin. publication_tasks may be owned by cappa_rw, so this
-- script switches role only for that table's indexes.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

GRANT CREATE ON SCHEMA public TO cappa_rw;

SET ROLE cappa_rw;

-- Celery monitor business task snapshot:
--   WHERE feature IN (...) AND status IN ('pending','processing')
--   ORDER BY updated_at DESC, created_at DESC
-- Existing (feature,status,updated_at,created_at) helps many cases, but this
-- partial index lets Postgres walk the active-task timeline directly.
CREATE INDEX IF NOT EXISTS ix_publication_tasks_active_business_updated_created_desc
    ON public.publication_tasks (updated_at DESC, created_at DESC)
    WHERE status IN ('pending', 'processing')
      AND feature IN (
          'publisher',
          'report_audio_workflow',
          'report_pdf_audio_workflow',
          'report_script_material_workflow',
          'report_top10_workflow',
          'mp3_video_workflow',
          'content_pdf_clean',
          'content_audio_clean',
          'zsxq_clean_output_publish'
      );

-- Keep the broader feature/status index present for filtered lists and older
-- planner choices.
CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_status_updated_created_desc
    ON public.publication_tasks (feature, status, updated_at DESC, created_at DESC);

ANALYZE public.publication_tasks;

RESET ROLE;

-- ZSXQ sync list and stale-run recovery indexes. These are included here as a
-- verification/repair block because today's slow log still shows zsxq_sync_runs
-- list and id-poll pressure.
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_created_at_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_status_updated
    ON public.zsxq_sync_runs (status, updated_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_status_updated
    ON public.zsxq_sync_runs (group_id, status, updated_at);

ANALYZE public.zsxq_sync_runs;
ANALYZE public.report_video_reports;
ANALYZE public.report_video_tasks;

-- ZSXQ workflow list/recovery hot paths:
--   WHERE status IN (...) ORDER BY created_at DESC
--   WHERE status/current_stage stale recovery ORDER BY updated_at/created_at
--   WHERE group_id/status ORDER BY created_at DESC
CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_status_updated_created
    ON public.zsxq_workflow_runs (status, updated_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_group_status_created
    ON public.zsxq_workflow_runs (group_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_stale_init
    ON public.zsxq_workflow_runs (status, current_stage, created_at ASC)
    WHERE status = 'queued' AND current_stage = 'init';

ANALYZE public.zsxq_workflow_runs;

-- Verification: expected hot indexes.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_publication_tasks_active_business_updated_created_desc',
      'ix_publication_tasks_feature_status_updated_created_desc',
      'ix_zsxq_sync_runs_group_created_at_desc',
      'ix_zsxq_sync_runs_status_updated',
      'ix_zsxq_sync_runs_group_status_updated',
      'ix_zsxq_workflow_runs_status_updated_created',
      'ix_zsxq_workflow_runs_group_status_created',
      'ix_zsxq_workflow_runs_stale_init',
      'uq_report_video_reports_task_code'
  )
ORDER BY tablename, indexname;

-- Verification: current table scan/index health.
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
      'zsxq_workflow_runs',
      'report_video_reports',
      'report_video_tasks'
  )
ORDER BY relname;
