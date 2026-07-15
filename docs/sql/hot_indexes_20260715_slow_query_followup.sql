-- Follow-up hot indexes for slow queries observed on 2026-07-15.
-- DBeaver-safe version: do not use CREATE INDEX CONCURRENTLY, because
-- some DBeaver execution modes pipeline statements and PostgreSQL rejects it.
--
-- Run as postgres/admin when the app is quiet. Keep Auto-commit ON.
-- If lock_timeout happens, stop workers/backend first and run again.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

-- Hot path:
--   services/zsxq_sync_service.py:_sync_run_summary_rows
--   WHERE group_id = ? ORDER BY created_at DESC LIMIT ?
CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_created_at_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC);

-- Hot paths:
--   services/platform_credential_service.py:list_credentials
--   WHERE platform = ? AND status = ?
--   ORDER BY platform ASC, priority ASC, updated_at DESC
CREATE INDEX IF NOT EXISTS ix_platform_credential_accounts_platform_status_priority_updated
    ON public.platform_credential_accounts (platform, status, priority ASC, updated_at DESC);

-- Hot paths:
--   services/platform_credential_service.py:resolve_credential_record
--   WHERE status = ? AND platform = ? [AND id = ?]
--   ORDER BY priority ASC, updated_at DESC
CREATE INDEX IF NOT EXISTS ix_platform_credential_accounts_status_platform_priority_updated
    ON public.platform_credential_accounts (status, platform, priority ASC, updated_at DESC);

ANALYZE public.zsxq_sync_runs;
ANALYZE public.platform_credential_accounts;

-- Verification: expected indexes.
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_zsxq_sync_runs_group_created_at_desc',
      'ix_platform_credential_accounts_platform_status_priority_updated',
      'ix_platform_credential_accounts_status_platform_priority_updated'
  )
ORDER BY tablename, indexname;

-- Verification: planner stats freshness and scan pressure.
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
      'zsxq_sync_runs',
      'platform_credential_accounts'
  )
ORDER BY relname;
