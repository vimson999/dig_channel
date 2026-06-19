-- ZSXQ sync run projection columns for DBeaver.
-- Run with the postgres/admin connection because zsxq_sync_runs is owned by postgres.
-- Safe to rerun. This script intentionally does NOT use CREATE INDEX CONCURRENTLY,
-- so DBeaver can execute it as a normal script.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_batch_id varchar(80);
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_attempt_id varchar(80);
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_round integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_max_rounds integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_trigger varchar(120);
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_queued_at varchar(64);
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS system_max_download_files integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS topics_fetched integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS topics_new integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS topics_updated integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS topics_existing integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS attachments_seen integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS attachments_downloaded integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS attachments_failed integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS attachments_skipped integer;
ALTER TABLE public.zsxq_sync_runs ADD COLUMN IF NOT EXISTS download_limit_reached boolean;

WITH source AS (
    SELECT
        id,
        NULLIF(result #>> '{system_sync,batch_id}', '') AS system_batch_id_text,
        NULLIF(result #>> '{system_sync,attempt_id}', '') AS system_attempt_id_text,
        NULLIF(result #>> '{system_sync,round}', '') AS system_round_text,
        NULLIF(result #>> '{system_sync,max_rounds}', '') AS system_max_rounds_text,
        NULLIF(result #>> '{system_sync,trigger}', '') AS system_trigger_text,
        NULLIF(result #>> '{system_sync,queued_at}', '') AS system_queued_at_text,
        NULLIF(result #>> '{system_sync,max_download_files}', '') AS system_max_download_files_text,
        NULLIF(COALESCE(result ->> 'topics_fetched', result #>> '{partial,topics_fetched}'), '') AS topics_fetched_text,
        NULLIF(COALESCE(result ->> 'topics_new', result #>> '{partial,topics_new}'), '') AS topics_new_text,
        NULLIF(COALESCE(result ->> 'topics_updated', result #>> '{partial,topics_updated}'), '') AS topics_updated_text,
        NULLIF(COALESCE(result ->> 'topics_existing', result #>> '{partial,topics_existing}'), '') AS topics_existing_text,
        NULLIF(COALESCE(result ->> 'attachments_seen', result #>> '{partial,attachments_seen}'), '') AS attachments_seen_text,
        NULLIF(COALESCE(result ->> 'attachments_downloaded', result #>> '{partial,attachments_downloaded}'), '') AS attachments_downloaded_text,
        NULLIF(COALESCE(result ->> 'attachments_failed', result #>> '{partial,attachments_failed}'), '') AS attachments_failed_text,
        NULLIF(COALESCE(result ->> 'attachments_skipped', result #>> '{partial,attachments_skipped}'), '') AS attachments_skipped_text,
        NULLIF(COALESCE(result ->> 'download_limit_reached', result #>> '{partial,download_limit_reached}'), '') AS download_limit_reached_text
    FROM public.zsxq_sync_runs
)
UPDATE public.zsxq_sync_runs AS run
SET
    system_batch_id = LEFT(source.system_batch_id_text, 80),
    system_attempt_id = LEFT(source.system_attempt_id_text, 80),
    system_round = CASE WHEN source.system_round_text ~ '^-?[0-9]+$' THEN source.system_round_text::integer ELSE NULL END,
    system_max_rounds = CASE WHEN source.system_max_rounds_text ~ '^-?[0-9]+$' THEN source.system_max_rounds_text::integer ELSE NULL END,
    system_trigger = LEFT(source.system_trigger_text, 120),
    system_queued_at = LEFT(source.system_queued_at_text, 64),
    system_max_download_files = CASE WHEN source.system_max_download_files_text ~ '^-?[0-9]+$' THEN source.system_max_download_files_text::integer ELSE NULL END,
    topics_fetched = CASE WHEN source.topics_fetched_text ~ '^-?[0-9]+$' THEN source.topics_fetched_text::integer ELSE 0 END,
    topics_new = CASE WHEN source.topics_new_text ~ '^-?[0-9]+$' THEN source.topics_new_text::integer ELSE 0 END,
    topics_updated = CASE WHEN source.topics_updated_text ~ '^-?[0-9]+$' THEN source.topics_updated_text::integer ELSE 0 END,
    topics_existing = CASE WHEN source.topics_existing_text ~ '^-?[0-9]+$' THEN source.topics_existing_text::integer ELSE 0 END,
    attachments_seen = CASE WHEN source.attachments_seen_text ~ '^-?[0-9]+$' THEN source.attachments_seen_text::integer ELSE 0 END,
    attachments_downloaded = CASE WHEN source.attachments_downloaded_text ~ '^-?[0-9]+$' THEN source.attachments_downloaded_text::integer ELSE 0 END,
    attachments_failed = CASE WHEN source.attachments_failed_text ~ '^-?[0-9]+$' THEN source.attachments_failed_text::integer ELSE 0 END,
    attachments_skipped = CASE WHEN source.attachments_skipped_text ~ '^-?[0-9]+$' THEN source.attachments_skipped_text::integer ELSE 0 END,
    download_limit_reached = CASE lower(source.download_limit_reached_text)
        WHEN 'true' THEN TRUE
        WHEN '1' THEN TRUE
        WHEN 'yes' THEN TRUE
        WHEN 'on' THEN TRUE
        WHEN 'false' THEN FALSE
        WHEN '0' THEN FALSE
        WHEN 'no' THEN FALSE
        WHEN 'off' THEN FALSE
        ELSE NULL
    END
FROM source
WHERE run.id = source.id;

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_group_created_desc
    ON public.zsxq_sync_runs (group_id, created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_sys_created_desc
    ON public.zsxq_sync_runs (created_at DESC)
    WHERE mode LIKE 'sys%';

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_system_batch_created_desc
    ON public.zsxq_sync_runs (system_batch_id, created_at DESC)
    WHERE mode LIKE 'sys%'
      AND system_batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_system_attempt_created_desc
    ON public.zsxq_sync_runs (system_attempt_id, created_at DESC)
    WHERE mode LIKE 'sys%'
      AND system_attempt_id IS NOT NULL;

ANALYZE public.zsxq_sync_runs;

SELECT
    count(*) AS sync_run_count,
    count(*) FILTER (WHERE mode LIKE 'sys%') AS system_run_count,
    count(*) FILTER (WHERE mode LIKE 'sys%' AND system_batch_id IS NULL) AS system_run_missing_batch_id,
    count(*) FILTER (WHERE mode LIKE 'sys%' AND topics_fetched IS NULL) AS system_run_missing_counts
FROM public.zsxq_sync_runs;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'zsxq_sync_runs'
  AND indexname IN (
      'ix_zsxq_sync_runs_sys_group_created_desc',
      'ix_zsxq_sync_runs_sys_created_desc',
      'ix_zsxq_sync_runs_system_batch_created_desc',
      'ix_zsxq_sync_runs_system_attempt_created_desc'
  )
ORDER BY indexname;
