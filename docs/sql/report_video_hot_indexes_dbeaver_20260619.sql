-- Hot indexes for the report-video slow SQL observed on 2026-06-19.
-- DBeaver-safe version: plain CREATE INDEX, no CONCURRENTLY, no transaction wrapper.
-- Run as a role that owns these tables or has CREATE privilege on schema public.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- Supports:
--   SELECT ... FROM report_video_reports ORDER BY created_at DESC LIMIT ...
CREATE INDEX IF NOT EXISTS ix_report_video_reports_created_at_desc
    ON public.report_video_reports (created_at DESC);

-- Supports report-video duplicate checks by PDF source hash without scanning raw_meta JSON in Python.
CREATE INDEX IF NOT EXISTS ix_report_video_reports_auto_asset_sha256
    ON public.report_video_reports ((raw_meta #>> '{auto_asset,source_sha256}'))
    WHERE (raw_meta #>> '{auto_asset,source_sha256}') IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_report_video_reports_auto_scan_sha256
    ON public.report_video_reports ((raw_meta #>> '{auto_scan,source_sha256}'))
    WHERE (raw_meta #>> '{auto_scan,source_sha256}') IS NOT NULL;

-- Backward-compatible lookup for older rows that only stored source hash on task.result.
CREATE INDEX IF NOT EXISTS ix_report_video_tasks_auto_asset_sha256
    ON public.report_video_tasks ((result #>> '{auto_asset,source_sha256}'))
    WHERE (result #>> '{auto_asset,source_sha256}') IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_report_video_tasks_auto_scan_sha256
    ON public.report_video_tasks ((result #>> '{auto_scan,source_sha256}'))
    WHERE (result #>> '{auto_scan,source_sha256}') IS NOT NULL;

-- Supports completed-video existence check:
--   WHERE task_id = ? AND status = 'completed'
CREATE INDEX IF NOT EXISTS ix_report_video_outputs_task_status
    ON public.report_video_outputs (task_id, status);

-- Supports system sync batch list:
--   WHERE mode LIKE 'sys%' AND group_id = ? ORDER BY created_at DESC LIMIT ?
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_group_created_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_created_desc
    ON public.zsxq_sync_runs (created_at DESC)
    WHERE mode LIKE 'sys%';

-- Supports Celery monitor active workflow polling:
--   WHERE status IN (...) ORDER BY updated_at DESC, created_at DESC LIMIT ?
CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_status_updated_created
    ON public.zsxq_workflow_runs (status, updated_at DESC, created_at DESC);

-- Supports project workflow gate:
--   WHERE group_id = ? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1
CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_group_status_created
    ON public.zsxq_workflow_runs (group_id, status, created_at DESC);

-- Supports stale queued/init recovery scan without sorting unrelated workflow rows.
CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_stale_init
    ON public.zsxq_workflow_runs (status, current_stage, created_at ASC)
    WHERE status = 'queued' AND current_stage = 'init';

ANALYZE public.report_video_reports;
ANALYZE public.report_video_tasks;
ANALYZE public.report_video_outputs;
ANALYZE public.zsxq_sync_runs;
ANALYZE public.zsxq_workflow_runs;

SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_report_video_reports_created_at_desc',
      'ix_report_video_reports_auto_asset_sha256',
      'ix_report_video_reports_auto_scan_sha256',
      'ix_report_video_tasks_auto_asset_sha256',
      'ix_report_video_tasks_auto_scan_sha256',
      'ix_report_video_outputs_task_status',
      'ix_zsxq_sync_runs_sys_group_created_desc',
      'ix_zsxq_sync_runs_sys_created_desc',
      'ix_zsxq_workflow_runs_status_updated_created',
      'ix_zsxq_workflow_runs_group_status_created',
      'ix_zsxq_workflow_runs_stale_init'
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
      'report_video_reports',
      'report_video_tasks',
      'report_video_outputs',
      'zsxq_sync_runs',
      'zsxq_workflow_runs'
  )
ORDER BY relname;
