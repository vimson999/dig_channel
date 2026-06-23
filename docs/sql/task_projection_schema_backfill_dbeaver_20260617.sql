-- Task/query projection schema migration for DBeaver.
-- Safe to rerun. Use a privileged user that can ALTER public.publication_tasks
-- and public.zsxq_attachments. This script intentionally does NOT use
-- CREATE INDEX CONCURRENTLY, so DBeaver can execute it as a normal script.

SET lock_timeout = '5s';
SET statement_timeout = '10min';

ALTER TABLE public.publication_tasks
    ALTER COLUMN current_step TYPE varchar(80);

ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS feature varchar(80);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS user_id varchar(64);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS parent_task_id varchar(36);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS source_task_id varchar(36);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS parent_feature varchar(80);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS source_feature varchar(80);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS task_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS task_video_path varchar(600);

ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_pdf_attachment_id varchar(36);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_pdf_filename varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_pdf_bytes varchar(40);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_pdf_topic_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_result_pdf_filename varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_result_pdf_bytes varchar(40);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_result_pdf_topic_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_metadata_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_video_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_cover_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_cover_subtitle varchar(220);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_structured_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_started_at varchar(64);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_completed_at varchar(64);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_error text;
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_output_path varchar(600);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_wav_path varchar(600);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_seconds varchar(40);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_audio_bytes varchar(40);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_video_enabled boolean;
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_video_status varchar(80);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_video_publisher_task_id varchar(36);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_video_message text;
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_video_path varchar(600);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_audio_cover_path varchar(600);

ALTER TABLE public.zsxq_attachments ADD COLUMN IF NOT EXISTS file_ext varchar(20);

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

-- Backfill publication task projection columns from existing JSON payloads.
UPDATE public.publication_tasks
SET
    feature = COALESCE(NULLIF(params ->> 'feature', ''), 'publisher'),
    user_id = LEFT(NULLIF(params ->> 'user_id', ''), 64),
    parent_task_id = LEFT(NULLIF(params ->> 'parent_task_id', ''), 36),
    source_task_id = LEFT(NULLIF(COALESCE(params ->> 'source_task_id', params #>> '{zsxq_publish,source_task_id}', result ->> 'source_task_id'), ''), 36),
    parent_feature = LEFT(NULLIF(params ->> 'parent_feature', ''), 80),
    source_feature = LEFT(NULLIF(params ->> 'source_feature', ''), 80),
    task_title = LEFT(NULLIF(COALESCE(
        params #>> '{bilibili,title}',
        params #>> '{thumbnail,main_title}',
        params #>> '{content_pipeline,title}',
        params #>> '{zsxq_publish,title}',
        params #>> '{report_audio_workflow,metadata,title}',
        params #>> '{report_audio_workflow,video,title}',
        result #>> '{structured_result,cover_title}',
        result #>> '{structured_result,title}',
        result #>> '{pdf,topic_title}',
        params #>> '{report_audio_workflow,pdf,topic_title}',
        params #>> '{report_audio_workflow,pdf,filename}'
    ), ''), 500),
    task_video_path = LEFT(NULLIF(result ->> 'video_path', ''), 600),
    report_audio_pdf_attachment_id = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,attachment_id}', ''), 36),
    report_audio_pdf_filename = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,filename}', ''), 500),
    report_audio_pdf_bytes = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,bytes}', ''), 40),
    report_audio_pdf_topic_title = LEFT(NULLIF(params #>> '{report_audio_workflow,pdf,topic_title}', ''), 500),
    report_audio_result_pdf_filename = LEFT(NULLIF(result #>> '{pdf,filename}', ''), 500),
    report_audio_result_pdf_bytes = LEFT(NULLIF(result #>> '{pdf,bytes}', ''), 40),
    report_audio_result_pdf_topic_title = LEFT(NULLIF(result #>> '{pdf,topic_title}', ''), 500),
    report_audio_metadata_title = LEFT(NULLIF(params #>> '{report_audio_workflow,metadata,title}', ''), 500),
    report_audio_video_title = LEFT(NULLIF(params #>> '{report_audio_workflow,video,title}', ''), 500),
    report_audio_cover_title = LEFT(NULLIF(result #>> '{structured_result,cover_title}', ''), 500),
    report_audio_cover_subtitle = LEFT(NULLIF(COALESCE(result #>> '{structured_result,cover_subtitle}', result #>> '{video,cover_subtitle}'), ''), 220),
    report_audio_structured_title = LEFT(NULLIF(result #>> '{structured_result,title}', ''), 500),
    report_audio_started_at = LEFT(NULLIF(result ->> 'started_at', ''), 64),
    report_audio_completed_at = LEFT(NULLIF(result ->> 'completed_at', ''), 64),
    report_audio_error = NULLIF(result ->> 'error', ''),
    report_audio_output_path = LEFT(NULLIF(result ->> 'output_path', ''), 600),
    report_audio_wav_path = LEFT(NULLIF(result #>> '{audio,wav_path}', ''), 600),
    report_audio_seconds = LEFT(NULLIF(result #>> '{audio,seconds}', ''), 40),
    report_audio_audio_bytes = LEFT(NULLIF(result #>> '{audio,bytes}', ''), 40),
    report_audio_video_enabled = CASE lower(NULLIF(result #>> '{video,enabled}', ''))
        WHEN 'true' THEN TRUE
        WHEN '1' THEN TRUE
        WHEN 'yes' THEN TRUE
        WHEN 'on' THEN TRUE
        WHEN 'false' THEN FALSE
        WHEN '0' THEN FALSE
        WHEN 'no' THEN FALSE
        WHEN 'off' THEN FALSE
        ELSE NULL
    END,
    report_audio_video_status = LEFT(NULLIF(result #>> '{video,status}', ''), 80),
    report_audio_video_publisher_task_id = LEFT(NULLIF(result #>> '{video,publisher_task_id}', ''), 36),
    report_audio_video_message = NULLIF(result #>> '{video,message}', ''),
    report_audio_video_path = LEFT(NULLIF(result #>> '{video,video_path}', ''), 600),
    report_audio_cover_path = LEFT(NULLIF(result #>> '{video,cover_path}', ''), 600);

-- Backfill attachment extension projection for PDF asset queries.
UPDATE public.zsxq_attachments
SET file_ext = LEFT(lower(COALESCE(
    substring(name from '[.]([^./]+)$'),
    substring(local_path from '[.]([^./]+)$')
)), 20);

-- Backfill ZSXQ sync run projection columns from existing JSON payloads.
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

ANALYZE public.publication_tasks;
ANALYZE public.zsxq_attachments;
ANALYZE public.zsxq_sync_runs;

-- Indexes for task list, child lookup, publish lookup, and report-audio asset stats.
CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_user_created_desc
    ON public.publication_tasks (feature, user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_created_desc_v2
    ON public.publication_tasks (feature, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_status_updated
    ON public.publication_tasks (feature, status, updated_at);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_status_updated_created_desc
    ON public.publication_tasks (feature, status, updated_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_publication_tasks_parent_created
    ON public.publication_tasks (parent_task_id, created_at)
    WHERE parent_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_source_created
    ON public.publication_tasks (source_task_id, created_at)
    WHERE source_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publication_tasks_report_audio_asset_created
    ON public.publication_tasks (report_audio_pdf_attachment_id, created_at DESC)
    WHERE feature = 'report_pdf_audio_workflow'
      AND report_audio_pdf_attachment_id IS NOT NULL;

-- Indexes for /api/report-audio-workflows/pdf-assets.
CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_pdf_assets_order
    ON public.zsxq_attachments (download_status, file_ext, updated_at DESC, created_at DESC)
    WHERE local_path IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_pdf_assets_group_tab
    ON public.zsxq_attachments (group_id, tab_id, download_status, file_ext, updated_at DESC, created_at DESC)
    WHERE local_path IS NOT NULL;

-- Indexes for ZSXQ batch/topic/attachment status pages.
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

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_status_updated
    ON public.zsxq_sync_runs (group_id, status, updated_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_status_updated
    ON public.zsxq_sync_runs (status, updated_at);

CREATE INDEX IF NOT EXISTS ix_zsxq_topics_group_tab_create_time_desc
    ON public.zsxq_topics (group_id, tab_id, create_time DESC, last_synced_at DESC);

CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_topic_created
    ON public.zsxq_attachments (group_id, tab_id, topic_id, created_at);

ANALYZE public.publication_tasks;
ANALYZE public.zsxq_sync_runs;
ANALYZE public.zsxq_topics;
ANALYZE public.zsxq_attachments;

-- Verification: projection coverage.
SELECT
    count(*) AS task_count,
    count(*) FILTER (WHERE feature IS NULL) AS missing_feature,
    count(*) FILTER (WHERE params ->> 'feature' = 'report_pdf_audio_workflow'
                     AND report_audio_pdf_filename IS NULL) AS report_audio_missing_pdf_projection,
    count(*) FILTER (WHERE (params ->> 'parent_task_id') IS NOT NULL
                     AND parent_task_id IS NULL) AS missing_parent_projection
FROM public.publication_tasks;

SELECT
    count(*) AS attachment_count,
    count(*) FILTER (WHERE file_ext = 'pdf') AS pdf_attachment_count,
    count(*) FILTER (WHERE file_ext IS NULL AND local_path IS NOT NULL) AS missing_file_ext
FROM public.zsxq_attachments;

SELECT
    count(*) AS sync_run_count,
    count(*) FILTER (WHERE mode LIKE 'sys%') AS system_run_count,
    count(*) FILTER (WHERE mode LIKE 'sys%' AND system_batch_id IS NULL) AS system_run_missing_batch_id,
    count(*) FILTER (WHERE mode LIKE 'sys%' AND topics_fetched IS NULL) AS system_run_missing_counts
FROM public.zsxq_sync_runs;

SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
      'ix_publication_tasks_feature_user_created_desc',
      'ix_publication_tasks_feature_created_desc_v2',
      'ix_publication_tasks_feature_status_updated',
      'ix_publication_tasks_feature_status_updated_created_desc',
      'ix_publication_tasks_parent_created',
      'ix_publication_tasks_source_created',
      'ix_publication_tasks_report_audio_asset_created',
      'ix_zsxq_attachments_pdf_assets_order',
      'ix_zsxq_attachments_pdf_assets_group_tab',
      'ix_zsxq_sync_runs_sys_group_created_desc',
      'ix_zsxq_sync_runs_sys_created_desc',
      'ix_zsxq_sync_runs_system_batch_created_desc',
      'ix_zsxq_sync_runs_system_attempt_created_desc',
      'ix_zsxq_sync_runs_group_status_updated',
      'ix_zsxq_sync_runs_status_updated'
  )
ORDER BY tablename, indexname;
