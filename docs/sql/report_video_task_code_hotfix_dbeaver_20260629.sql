SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- Hot path seen in slow_query.log:
--   SELECT ... FROM report_video_reports
--   WHERE task_id = ?
--   ORDER BY report_code ASC;
--
-- The unique constraint uq_report_video_reports_task_code should already provide
-- a btree index on (task_id, report_code). This explicit index is a defensive
-- hotfix for environments where the table was created before that constraint or
-- the index is missing.
CREATE INDEX IF NOT EXISTS ix_report_video_reports_task_code
    ON public.report_video_reports (task_id, report_code);

ANALYZE public.report_video_reports;

-- Verification: expected index/constraint coverage for the hot path.
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'report_video_reports'
  AND (
      indexname = 'ix_report_video_reports_task_code'
      OR indexname = 'uq_report_video_reports_task_code'
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
  AND relname = 'report_video_reports';
