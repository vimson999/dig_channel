-- Content-pipeline and ZSXQ run-download structural migration.
-- Run the whole file in DBeaver as postgres/admin. Auto-commit may stay enabled;
-- the explicit transaction below makes the migration all-or-nothing.
-- This script intentionally avoids CREATE INDEX CONCURRENTLY so DBeaver may
-- execute the whole selection as one pipeline.
-- Stop backend/Celery/schedulers first; ALTER TABLE needs a short exclusive lock.
-- Current runtime also requires the follow-up
-- bilibili_upload_execution_projection_dbeaver_20260718.sql after this file.

BEGIN;

SET LOCAL lock_timeout = '60s';
SET LOCAL statement_timeout = '20min';

-- A waiting ALTER TABLE lock can block otherwise fast SELECTs behind it. Do
-- not run structural DDL while any application connection is still alive.
DO $migration_guard$
DECLARE
    app_connections integer;
BEGIN
    SELECT count(*)
    INTO app_connections
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND pid <> pg_backend_pid()
      AND usename = 'cappa_rw';

    IF app_connections > 0 THEN
        RAISE EXCEPTION
            'Refusing online DDL: % cappa_rw connection(s) still attached. Stop backend/Celery/schedulers first.',
            app_connections;
    END IF;
END
$migration_guard$;

SET ROLE cappa_rw;

ALTER TABLE public.publication_tasks
    ADD COLUMN IF NOT EXISTS content_clean_output_dir varchar(600),
    ADD COLUMN IF NOT EXISTS content_clean_files_count integer,
    ADD COLUMN IF NOT EXISTS content_clean_success_count integer,
    ADD COLUMN IF NOT EXISTS content_clean_failed_count integer,
    ADD COLUMN IF NOT EXISTS content_clean_message text;

-- Backfill bounded terminal fields. The complete result JSON remains unchanged.
WITH source AS (
    SELECT
        id,
        COALESCE(result::jsonb, '{}'::jsonb) AS payload
    FROM public.publication_tasks
    WHERE feature IN ('content_pdf_clean', 'content_audio_clean')
)
UPDATE public.publication_tasks AS task
SET
    content_clean_output_dir = NULLIF(source.payload ->> 'local_output_dir', ''),
    content_clean_files_count = CASE
        WHEN source.payload ->> 'files_count' ~ '^\d+$'
            THEN (source.payload ->> 'files_count')::integer
        WHEN jsonb_typeof(source.payload -> 'cleaned_files') = 'array'
            THEN jsonb_array_length(source.payload -> 'cleaned_files')
        WHEN jsonb_typeof(source.payload -> 'files') = 'array'
            THEN jsonb_array_length(source.payload -> 'files')
        ELSE 0
    END,
    content_clean_success_count = CASE
        WHEN source.payload ->> 'success_count' ~ '^\d+$'
            THEN (source.payload ->> 'success_count')::integer
        WHEN jsonb_typeof(source.payload -> 'cleaned_files') = 'array'
            THEN jsonb_array_length(source.payload -> 'cleaned_files')
        ELSE 0
    END,
    content_clean_failed_count = CASE
        WHEN source.payload ->> 'failed_count' ~ '^\d+$'
            THEN (source.payload ->> 'failed_count')::integer
        ELSE 0
    END,
    content_clean_message = NULLIF(source.payload ->> 'message', '')
FROM source
WHERE task.id = source.id;

-- These `_asc` indexes duplicate the older canonical indexes byte-for-byte.
-- Keeping both only splits planner statistics and doubles write maintenance.
DROP INDEX IF EXISTS public.ix_publication_tasks_parent_created_asc;
DROP INDEX IF EXISTS public.ix_publication_tasks_source_created_asc;

ANALYZE public.publication_tasks;
RESET ROLE;

-- Bilibili upload children are a stable business entity, not an opaque task
-- payload. New writes populate this one-to-one table in the same transaction;
-- history/list polling reads it with one bounded JOIN and never opens task JSON.
CREATE TABLE IF NOT EXISTS public.publication_task_bilibili_uploads (
    task_id varchar(36) PRIMARY KEY
        REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
    parent_task_id varchar(36) NOT NULL,
    parent_feature varchar(80) NOT NULL,
    source_label varchar(160) NOT NULL,
    video_key varchar(32),
    cover_key varchar(80),
    title varchar(120),
    credential_id varchar(80),
    credential_name varchar(255),
    charging_enabled boolean NOT NULL DEFAULT false,
    preview_start_time integer,
    preview_end_time integer,
    upower_level_id varchar(80),
    upower_unit_price integer,
    upower_mode integer,
    member_first_exp_time integer,
    delay_time bigint,
    season_id bigint,
    aid varchar(32),
    bvid varchar(32),
    queue_repair_count integer NOT NULL DEFAULT 0,
    queue_repair_last_at timestamp without time zone,
    queue_repair_previous_celery_id varchar(50),
    queue_repair_current_celery_id varchar(50),
    queue_repair_queue varchar(80),
    queue_repair_reason varchar(160),
    queue_repair_previous_message_visible boolean,
    canceled_at timestamp without time zone,
    cancel_reason varchar(160),
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    updated_at timestamp without time zone NOT NULL DEFAULT now()
);

-- Repair an early/partial draft of this table as well. CREATE TABLE IF NOT
-- EXISTS alone does not add columns that were introduced after the draft.
ALTER TABLE public.publication_task_bilibili_uploads
    ADD COLUMN IF NOT EXISTS queue_repair_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS queue_repair_last_at timestamp without time zone,
    ADD COLUMN IF NOT EXISTS queue_repair_previous_celery_id varchar(50),
    ADD COLUMN IF NOT EXISTS queue_repair_current_celery_id varchar(50),
    ADD COLUMN IF NOT EXISTS queue_repair_queue varchar(80),
    ADD COLUMN IF NOT EXISTS queue_repair_reason varchar(160),
    ADD COLUMN IF NOT EXISTS queue_repair_previous_message_visible boolean,
    ADD COLUMN IF NOT EXISTS canceled_at timestamp without time zone,
    ADD COLUMN IF NOT EXISTS cancel_reason varchar(160);

CREATE INDEX IF NOT EXISTS ix_publication_task_bili_uploads_parent_source_created
    ON public.publication_task_bilibili_uploads (
        parent_task_id, parent_feature, source_label, created_at DESC
    );

GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.publication_task_bilibili_uploads
    TO cappa_rw;

-- One-time historical recovery. JSON is read only during migration; every hot
-- field subsequently comes from typed columns.
WITH raw AS (
    SELECT
        task.*,
        COALESCE(task.params::jsonb, '{}'::jsonb) AS params_payload,
        COALESCE(task.result::jsonb, '{}'::jsonb) AS result_payload
    FROM public.publication_tasks AS task
    WHERE task.feature = 'publisher'
),
source AS (
    SELECT
        raw.*,
        COALESCE(
            NULLIF(raw.result_payload ->> 'bilibili_credential_name', ''),
            NULLIF(raw.params_payload #>> '{bilibili,credential_name}', ''),
            NULLIF(account.owner_label, ''),
            NULLIF(account.name, ''),
            NULLIF(account.masked_summary, ''),
            account.id
        ) AS credential_display_name,
        COALESCE(
            raw.params_payload #> '{bilibili,charging_pay}',
            raw.result_payload -> 'bilibili_charging_pay',
            '{}'::jsonb
        ) AS charging_payload
    FROM raw
    LEFT JOIN public.platform_credential_accounts AS account
        ON account.id = COALESCE(
            NULLIF(raw.params_payload #>> '{bilibili,credential_id}', ''),
            NULLIF(raw.result_payload ->> 'bilibili_credential_id', '')
        )
    WHERE COALESCE(
        raw.params_payload #>> '{metadata,source}',
        raw.result_payload #>> '{metadata,source}',
        ''
    ) LIKE '%:bilibili_upload'
)
INSERT INTO public.publication_task_bilibili_uploads (
    task_id,
    parent_task_id,
    parent_feature,
    source_label,
    video_key,
    cover_key,
    title,
    credential_id,
    credential_name,
    charging_enabled,
    preview_start_time,
    preview_end_time,
    upower_level_id,
    upower_unit_price,
    upower_mode,
    member_first_exp_time,
    delay_time,
    season_id,
    aid,
    bvid,
    queue_repair_count,
    queue_repair_last_at,
    queue_repair_previous_celery_id,
    queue_repair_current_celery_id,
    queue_repair_queue,
    queue_repair_reason,
    queue_repair_previous_message_visible,
    canceled_at,
    cancel_reason,
    created_at,
    updated_at
)
SELECT
    source.id,
    COALESCE(
        source.parent_task_id,
        NULLIF(source.params_payload ->> 'parent_task_id', ''),
        NULLIF(source.params_payload ->> 'source_task_id', '')
    ),
    COALESCE(
        source.parent_feature,
        NULLIF(source.params_payload ->> 'parent_feature', ''),
        NULLIF(source.params_payload ->> 'source_feature', ''),
        'unknown'
    ),
    COALESCE(
        NULLIF(source.params_payload #>> '{metadata,source}', ''),
        NULLIF(source.result_payload #>> '{metadata,source}', '')
    ),
    NULLIF(source.params_payload #>> '{metadata,video_key}', ''),
    COALESCE(
        NULLIF(source.params_payload #>> '{metadata,cover_key}', ''),
        NULLIF(source.params_payload #>> '{bilibili,cover_key}', '')
    ),
    LEFT(NULLIF(COALESCE(
        source.params_payload #>> '{bilibili,title}',
        source.result_payload ->> 'bilibili_title'
    ), ''), 120),
    LEFT(NULLIF(COALESCE(
        source.params_payload #>> '{bilibili,credential_id}',
        source.result_payload ->> 'bilibili_credential_id'
    ), ''), 80),
    LEFT(NULLIF(source.credential_display_name, ''), 255),
    lower(COALESCE(source.charging_payload ->> 'enabled', 'false'))
        IN ('1', 'true', 'yes', 'on'),
    CASE
        WHEN COALESCE(
            source.charging_payload ->> 'preview_start_time',
            source.charging_payload #>> '{preview,start_time}'
        ) ~ '^\d+$'
        THEN COALESCE(
            source.charging_payload ->> 'preview_start_time',
            source.charging_payload #>> '{preview,start_time}'
        )::integer
    END,
    CASE
        WHEN COALESCE(
            source.charging_payload ->> 'preview_end_time',
            source.charging_payload #>> '{preview,end_time}'
        ) ~ '^\d+$'
        THEN COALESCE(
            source.charging_payload ->> 'preview_end_time',
            source.charging_payload #>> '{preview,end_time}'
        )::integer
    END,
    LEFT(NULLIF(source.charging_payload ->> 'upower_level_id', ''), 80),
    CASE WHEN source.charging_payload ->> 'upower_unit_price' ~ '^\d+$'
        THEN (source.charging_payload ->> 'upower_unit_price')::integer END,
    CASE WHEN source.charging_payload ->> 'upower_mode' ~ '^\d+$'
        THEN (source.charging_payload ->> 'upower_mode')::integer END,
    CASE WHEN source.charging_payload #>> '{member_first,exp_time}' ~ '^\d+$'
        THEN (source.charging_payload #>> '{member_first,exp_time}')::integer END,
    CASE
        WHEN COALESCE(
            source.params_payload #>> '{bilibili,delay_time}',
            source.result_payload ->> 'bilibili_delay_time'
        ) ~ '^\d+$'
        THEN COALESCE(
            source.params_payload #>> '{bilibili,delay_time}',
            source.result_payload ->> 'bilibili_delay_time'
        )::bigint
    END,
    CASE
        WHEN COALESCE(
            source.params_payload #>> '{bilibili,season_id}',
            source.result_payload ->> 'bilibili_season_id'
        ) ~ '^\d+$'
        THEN COALESCE(
            source.params_payload #>> '{bilibili,season_id}',
            source.result_payload ->> 'bilibili_season_id'
        )::bigint
    END,
    LEFT(NULLIF(source.result_payload #>> '{bilibili_result,aid}', ''), 32),
    LEFT(NULLIF(source.result_payload #>> '{bilibili_result,bvid}', ''), 32),
    CASE WHEN source.result_payload #>> '{queue_repair,count}' ~ '^\d+$'
        THEN (source.result_payload #>> '{queue_repair,count}')::integer
        ELSE 0 END,
    CASE
        WHEN source.result_payload #>> '{queue_repair,last_requeued_at}'
            ~ '^\d{4}-\d{2}-\d{2}'
        THEN (source.result_payload #>> '{queue_repair,last_requeued_at}')::timestamp
    END,
    LEFT(NULLIF(source.result_payload #>> '{queue_repair,previous_celery_id}', ''), 50),
    LEFT(NULLIF(source.result_payload #>> '{queue_repair,current_celery_id}', ''), 50),
    LEFT(NULLIF(source.result_payload #>> '{queue_repair,queue}', ''), 80),
    LEFT(NULLIF(source.result_payload #>> '{queue_repair,reason}', ''), 160),
    CASE
        WHEN source.result_payload #> '{queue_repair,previous_message_visible_in_queue}' IS NULL
            THEN NULL
        ELSE lower(source.result_payload #>> '{queue_repair,previous_message_visible_in_queue}')
            IN ('1', 'true', 'yes', 'on')
    END,
    CASE
        WHEN source.result_payload ->> 'bilibili_upload_canceled_at'
            ~ '^\d{4}-\d{2}-\d{2}'
        THEN (source.result_payload ->> 'bilibili_upload_canceled_at')::timestamp
    END,
    LEFT(NULLIF(source.result_payload ->> 'bilibili_upload_cancel_reason', ''), 160),
    COALESCE(source.created_at, now()),
    COALESCE(source.updated_at, source.created_at, now())
FROM source
WHERE COALESCE(
    source.parent_task_id,
    NULLIF(source.params_payload ->> 'parent_task_id', ''),
    NULLIF(source.params_payload ->> 'source_task_id', '')
) IS NOT NULL
ON CONFLICT (task_id) DO UPDATE SET
    parent_task_id = EXCLUDED.parent_task_id,
    parent_feature = EXCLUDED.parent_feature,
    source_label = EXCLUDED.source_label,
    video_key = EXCLUDED.video_key,
    cover_key = EXCLUDED.cover_key,
    title = EXCLUDED.title,
    credential_id = EXCLUDED.credential_id,
    credential_name = EXCLUDED.credential_name,
    charging_enabled = EXCLUDED.charging_enabled,
    preview_start_time = EXCLUDED.preview_start_time,
    preview_end_time = EXCLUDED.preview_end_time,
    upower_level_id = EXCLUDED.upower_level_id,
    upower_unit_price = EXCLUDED.upower_unit_price,
    upower_mode = EXCLUDED.upower_mode,
    member_first_exp_time = EXCLUDED.member_first_exp_time,
    delay_time = EXCLUDED.delay_time,
    season_id = EXCLUDED.season_id,
    aid = EXCLUDED.aid,
    bvid = EXCLUDED.bvid,
    queue_repair_count = EXCLUDED.queue_repair_count,
    queue_repair_last_at = EXCLUDED.queue_repair_last_at,
    queue_repair_previous_celery_id = EXCLUDED.queue_repair_previous_celery_id,
    queue_repair_current_celery_id = EXCLUDED.queue_repair_current_celery_id,
    queue_repair_queue = EXCLUDED.queue_repair_queue,
    queue_repair_reason = EXCLUDED.queue_repair_reason,
    queue_repair_previous_message_visible = EXCLUDED.queue_repair_previous_message_visible,
    canceled_at = EXCLUDED.canceled_at,
    cancel_reason = EXCLUDED.cancel_reason,
    updated_at = EXCLUDED.updated_at;

ANALYZE public.publication_task_bilibili_uploads;

ALTER TABLE public.zsxq_sync_runs
    ADD COLUMN IF NOT EXISTS download_ledger_complete boolean NOT NULL DEFAULT false;

-- This hotfix index duplicates the canonical Alembic-managed
-- ix_zsxq_topics_group_tab_create_time_desc definition.
DROP INDEX IF EXISTS public.ix_zsxq_topics_group_tab_create_synced_desc;

CREATE TABLE IF NOT EXISTS public.zsxq_sync_run_downloads (
    run_id varchar(36) NOT NULL
        REFERENCES public.zsxq_sync_runs(id) ON DELETE CASCADE,
    attachment_id varchar(36) NOT NULL
        REFERENCES public.zsxq_attachments(id) ON DELETE CASCADE,
    group_id varchar(32) NOT NULL,
    tab_id varchar(128) NOT NULL,
    topic_id varchar(64) NOT NULL,
    name varchar(500),
    local_path varchar(600) NOT NULL,
    file_ext varchar(20),
    downloaded_at timestamp without time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, attachment_id)
);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_run_downloads_run_ext
    ON public.zsxq_sync_run_downloads (run_id, file_ext);

CREATE INDEX IF NOT EXISTS ix_zsxq_sync_run_downloads_attachment
    ON public.zsxq_sync_run_downloads (attachment_id);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.zsxq_sync_run_downloads
    TO cappa_rw;

-- Recover historical run-to-file facts from the old audit JSON. Attachment
-- columns are authoritative; JSON is used only to identify the historical row.
WITH run_json AS (
    SELECT
        run.id AS run_id,
        run.group_id,
        COALESCE(run.completed_at, run.updated_at, run.created_at, now()) AS downloaded_at,
        COALESCE(run.result::jsonb, '{}'::jsonb) AS payload
    FROM public.zsxq_sync_runs AS run
),
raw_items AS (
    SELECT run_json.*, NULL::text AS tab_hint, item.value AS item
    FROM run_json
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(run_json.payload -> 'downloaded_files') = 'array'
                THEN run_json.payload -> 'downloaded_files'
            ELSE '[]'::jsonb
        END
    ) AS item

    UNION ALL

    SELECT run_json.*, NULL::text AS tab_hint, item.value AS item
    FROM run_json
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(run_json.payload -> 'downloaded_pdf_files') = 'array'
                THEN run_json.payload -> 'downloaded_pdf_files'
            ELSE '[]'::jsonb
        END
    ) AS item

    UNION ALL

    SELECT
        run_json.*,
        COALESCE(tab.value ->> 'tab_id', tab.value ->> 'id') AS tab_hint,
        item.value AS item
    FROM run_json
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(run_json.payload -> 'tabs') = 'array'
                THEN run_json.payload -> 'tabs'
            ELSE '[]'::jsonb
        END
    ) AS tab
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(tab.value -> 'downloaded_files') = 'array'
                THEN tab.value -> 'downloaded_files'
            ELSE '[]'::jsonb
        END
    ) AS item

    UNION ALL

    SELECT
        run_json.*,
        COALESCE(tab.value ->> 'tab_id', tab.value ->> 'id') AS tab_hint,
        item.value AS item
    FROM run_json
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(run_json.payload -> 'tabs') = 'array'
                THEN run_json.payload -> 'tabs'
            ELSE '[]'::jsonb
        END
    ) AS tab
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(tab.value -> 'downloaded_pdf_files') = 'array'
                THEN tab.value -> 'downloaded_pdf_files'
            ELSE '[]'::jsonb
        END
    ) AS item
),
normalized AS (
    SELECT
        run_id,
        group_id,
        downloaded_at,
        NULLIF(item ->> 'attachment_id', '') AS payload_attachment_id,
        COALESCE(NULLIF(item ->> 'tab_id', ''), NULLIF(tab_hint, '')) AS tab_hint,
        NULLIF(item ->> 'topic_id', '') AS topic_hint,
        NULLIF(item ->> 'name', '') AS name_hint,
        NULLIF(
            CASE
                WHEN jsonb_typeof(item) = 'object'
                    THEN COALESCE(item ->> 'path', item ->> 'local_path')
                WHEN jsonb_typeof(item) = 'string'
                    THEN item #>> '{}'
                ELSE NULL
            END,
            ''
        ) AS local_path_hint
    FROM raw_items
),
matched AS (
    SELECT
        normalized.run_id,
        attachment.id AS attachment_id,
        attachment.group_id,
        attachment.tab_id,
        attachment.topic_id,
        COALESCE(attachment.name, normalized.name_hint) AS name,
        COALESCE(attachment.local_path, normalized.local_path_hint) AS local_path,
        COALESCE(
            NULLIF(lower(attachment.file_ext), ''),
            NULLIF(lower(substring(
                COALESCE(attachment.local_path, normalized.local_path_hint, attachment.name)
                FROM '[.]([^./]+)$'
            )), '')
        ) AS file_ext,
        normalized.downloaded_at
    FROM normalized
    JOIN LATERAL (
        SELECT candidate.*
        FROM public.zsxq_attachments AS candidate
        WHERE candidate.group_id = normalized.group_id
          AND (
              (
                  normalized.payload_attachment_id IS NOT NULL
                  AND candidate.id = normalized.payload_attachment_id
              )
              OR (
                  normalized.payload_attachment_id IS NULL
                  AND (
                      candidate.local_path = normalized.local_path_hint
                      OR (
                          candidate.local_path IS NOT NULL
                          AND normalized.local_path_hint LIKE '%' || candidate.local_path
                      )
                      OR (
                          normalized.topic_hint IS NOT NULL
                          AND candidate.topic_id = normalized.topic_hint
                          AND (normalized.tab_hint IS NULL OR candidate.tab_id = normalized.tab_hint)
                          AND (normalized.name_hint IS NULL OR candidate.name = normalized.name_hint)
                      )
                  )
              )
          )
        ORDER BY
            CASE WHEN candidate.id = normalized.payload_attachment_id THEN 0 ELSE 1 END,
            candidate.updated_at DESC NULLS LAST,
            candidate.created_at DESC
        LIMIT 1
    ) AS attachment ON true
    WHERE COALESCE(attachment.local_path, normalized.local_path_hint) IS NOT NULL
),
deduplicated AS (
    SELECT DISTINCT ON (run_id, attachment_id)
        run_id,
        attachment_id,
        group_id,
        tab_id,
        topic_id,
        name,
        local_path,
        file_ext,
        downloaded_at
    FROM matched
    ORDER BY run_id, attachment_id, downloaded_at DESC
)
INSERT INTO public.zsxq_sync_run_downloads (
    run_id,
    attachment_id,
    group_id,
    tab_id,
    topic_id,
    name,
    local_path,
    file_ext,
    downloaded_at
)
SELECT
    run_id,
    attachment_id,
    group_id,
    tab_id,
    topic_id,
    name,
    local_path,
    file_ext,
    downloaded_at
FROM deduplicated
ON CONFLICT (run_id, attachment_id) DO UPDATE SET
    group_id = EXCLUDED.group_id,
    tab_id = EXCLUDED.tab_id,
    topic_id = EXCLUDED.topic_id,
    name = EXCLUDED.name,
    local_path = EXCLUDED.local_path,
    file_ext = EXCLUDED.file_ext,
    downloaded_at = EXCLUDED.downloaded_at;

-- Mark only demonstrably complete historical ledgers. New successful sync runs
-- set this flag atomically at their terminal commit.
WITH run_json AS (
    SELECT id AS run_id, COALESCE(result::jsonb, '{}'::jsonb) AS payload
    FROM public.zsxq_sync_runs
),
raw_items AS (
    SELECT run_id, item
    FROM run_json
    CROSS JOIN LATERAL jsonb_path_query(payload, '$.downloaded_files[*]') AS item

    UNION ALL

    SELECT run_id, item
    FROM run_json
    CROSS JOIN LATERAL jsonb_path_query(payload, '$.downloaded_pdf_files[*]') AS item

    UNION ALL

    SELECT run_id, item
    FROM run_json
    CROSS JOIN LATERAL jsonb_path_query(payload, '$.tabs[*].downloaded_files[*]') AS item

    UNION ALL

    SELECT run_id, item
    FROM run_json
    CROSS JOIN LATERAL jsonb_path_query(payload, '$.tabs[*].downloaded_pdf_files[*]') AS item
),
expected AS (
    SELECT
        run_id,
        count(DISTINCT CASE
            WHEN jsonb_typeof(item) = 'object' THEN COALESCE(
                NULLIF(item ->> 'attachment_id', ''),
                NULLIF(item ->> 'path', ''),
                NULLIF(item ->> 'local_path', '')
            )
            WHEN jsonb_typeof(item) = 'string' THEN NULLIF(item #>> '{}', '')
            ELSE NULL
        END) AS expected_items
    FROM raw_items
    GROUP BY run_id
)
UPDATE public.zsxq_sync_runs AS run
SET download_ledger_complete = CASE
    WHEN run.status <> 'completed' THEN false
    ELSE (
        SELECT count(*)
        FROM public.zsxq_sync_run_downloads AS ledger
        WHERE ledger.run_id = run.id
    ) >= GREATEST(
        COALESCE(run.attachments_downloaded, 0),
        COALESCE((
            SELECT expected.expected_items
            FROM expected
            WHERE expected.run_id = run.id
        ), 0)
    )
END;

-- Historical workflow summaries duplicated dispatch config/items that already
-- remain in zsxq_sync_runs.result and child task rows. Remove only duplicates;
-- status, counts, errors and task ids stay intact.
WITH candidates AS (
    SELECT
        id,
        stage_results::jsonb AS payload,
        stage_results::jsonb #> '{sync,summary,auto_script_material_dispatch}' AS dispatch
    FROM public.zsxq_workflow_runs
    WHERE jsonb_typeof(
        stage_results::jsonb #> '{sync,summary,auto_script_material_dispatch}'
    ) = 'object'
)
UPDATE public.zsxq_workflow_runs AS workflow
SET stage_results = jsonb_set(
    candidates.payload,
    '{sync,summary,auto_script_material_dispatch}',
    (candidates.dispatch - 'config' - 'items')
        || CASE
            WHEN jsonb_typeof(candidates.dispatch -> 'pdf') = 'object'
                THEN jsonb_build_object('pdf', (candidates.dispatch -> 'pdf') - 'items')
            ELSE '{}'::jsonb
        END
        || CASE
            WHEN jsonb_typeof(candidates.dispatch -> 'mp3') = 'object'
                THEN jsonb_build_object('mp3', (candidates.dispatch -> 'mp3') - 'items')
            ELSE '{}'::jsonb
        END,
    false
)
FROM candidates
WHERE workflow.id = candidates.id;

WITH candidates AS (
    SELECT
        id,
        stage_results::jsonb AS payload,
        stage_results::jsonb #> '{sync,summary,auto_video_dispatch}' AS dispatch
    FROM public.zsxq_workflow_runs
    WHERE jsonb_typeof(
        stage_results::jsonb #> '{sync,summary,auto_video_dispatch}'
    ) = 'object'
)
UPDATE public.zsxq_workflow_runs AS workflow
SET stage_results = jsonb_set(
    candidates.payload,
    '{sync,summary,auto_video_dispatch}',
    (candidates.dispatch - 'config' - 'items')
        || CASE
            WHEN jsonb_typeof(candidates.dispatch -> 'pdf') = 'object'
                THEN jsonb_build_object('pdf', (candidates.dispatch -> 'pdf') - 'items')
            ELSE '{}'::jsonb
        END
        || CASE
            WHEN jsonb_typeof(candidates.dispatch -> 'mp3') = 'object'
                THEN jsonb_build_object('mp3', (candidates.dispatch -> 'mp3') - 'items')
            ELSE '{}'::jsonb
        END,
    false
)
FROM candidates
WHERE workflow.id = candidates.id;

ANALYZE public.zsxq_sync_run_downloads;
ANALYZE public.zsxq_workflow_runs;

-- Verification 1: all five clean projections exist.
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'publication_tasks'
  AND column_name IN (
      'content_clean_output_dir',
      'content_clean_files_count',
      'content_clean_success_count',
      'content_clean_failed_count',
      'content_clean_message'
  )
ORDER BY column_name;

-- Verification 2: clean tasks and populated projections.
SELECT
    count(*) AS clean_tasks,
    count(*) FILTER (WHERE content_clean_output_dir IS NOT NULL) AS with_output_dir,
    count(*) FILTER (WHERE content_clean_files_count IS NOT NULL) AS with_files_count
FROM public.publication_tasks
WHERE feature IN ('content_pdf_clean', 'content_audio_clean');

-- Verification 3: ledger rows and run coverage.
SELECT
    count(*) AS ledger_rows,
    count(DISTINCT run_id) AS covered_runs,
    count(DISTINCT attachment_id) AS covered_attachments
FROM public.zsxq_sync_run_downloads;

-- Verification 4: zero means historical run coverage is complete. Nonzero is
-- business-safe because code keeps a single-row legacy fallback, but should be
-- investigated before declaring the migration fully complete.
WITH coverage AS (
    SELECT run_id, count(*) AS ledger_rows
    FROM public.zsxq_sync_run_downloads
    GROUP BY run_id
)
SELECT
    count(*) AS incomplete_run_count,
    COALESCE(sum(
        GREATEST(
            COALESCE(run.attachments_downloaded, 0) - COALESCE(coverage.ledger_rows, 0),
            0
        )
    ), 0) AS missing_ledger_rows
FROM public.zsxq_sync_runs AS run
LEFT JOIN coverage ON coverage.run_id = run.id
WHERE COALESCE(run.attachments_downloaded, 0) > COALESCE(coverage.ledger_rows, 0);

SELECT
    count(*) FILTER (WHERE status = 'completed') AS completed_runs,
    count(*) FILTER (
        WHERE status = 'completed' AND download_ledger_complete
    ) AS completed_runs_with_complete_ledger,
    count(*) FILTER (
        WHERE status = 'completed' AND NOT download_ledger_complete
    ) AS completed_runs_using_legacy_fallback
FROM public.zsxq_sync_runs;

-- Verification 5: application privilege must be true.
SELECT has_table_privilege(
    'cappa_rw',
    'public.zsxq_sync_run_downloads',
    'SELECT, INSERT, UPDATE, DELETE'
) AS cappa_rw_can_use_download_ledger;

-- Verification 6: recent workflow payload size after compaction.
SELECT
    id,
    status,
    pg_column_size(stage_results::jsonb) AS stage_results_bytes,
    created_at
FROM public.zsxq_workflow_runs
ORDER BY created_at DESC
LIMIT 10;

-- Verification 7: both counts must be zero after historical compaction.
SELECT
    count(*) FILTER (
        WHERE stage_results::jsonb #> '{sync,summary,auto_script_material_dispatch,config}' IS NOT NULL
           OR stage_results::jsonb #> '{sync,summary,auto_script_material_dispatch,items}' IS NOT NULL
           OR stage_results::jsonb #> '{sync,summary,auto_script_material_dispatch,pdf,items}' IS NOT NULL
           OR stage_results::jsonb #> '{sync,summary,auto_script_material_dispatch,mp3,items}' IS NOT NULL
    ) AS script_dispatches_with_duplicate_payload,
    count(*) FILTER (
        WHERE stage_results::jsonb #> '{sync,summary,auto_video_dispatch,config}' IS NOT NULL
           OR stage_results::jsonb #> '{sync,summary,auto_video_dispatch,items}' IS NOT NULL
           OR stage_results::jsonb #> '{sync,summary,auto_video_dispatch,pdf,items}' IS NOT NULL
           OR stage_results::jsonb #> '{sync,summary,auto_video_dispatch,mp3,items}' IS NOT NULL
    ) AS video_dispatches_with_duplicate_payload
FROM public.zsxq_workflow_runs;

-- Verification 8: duplicate hotfix indexes must be gone while their canonical
-- equivalents remain present.
SELECT
    to_regclass('public.ix_publication_tasks_parent_created') AS parent_index,
    to_regclass('public.ix_publication_tasks_parent_created_asc') AS duplicate_parent_index,
    to_regclass('public.ix_publication_tasks_source_created') AS source_index,
    to_regclass('public.ix_publication_tasks_source_created_asc') AS duplicate_source_index,
    to_regclass('public.ix_zsxq_topics_group_tab_create_time_desc') AS topic_index,
    to_regclass('public.ix_zsxq_topics_group_tab_create_synced_desc') AS duplicate_topic_index;

-- Verification 9: all historical Bilibili upload children must have exactly
-- one structured row; privilege and the parent/source index must also exist.
WITH expected AS (
    SELECT task.id
    FROM public.publication_tasks AS task
    WHERE task.feature = 'publisher'
      AND COALESCE(
          task.params::jsonb #>> '{metadata,source}',
          task.result::jsonb #>> '{metadata,source}',
          ''
      ) LIKE '%:bilibili_upload'
)
SELECT
    count(*) AS expected_upload_tasks,
    count(projection.task_id) AS structured_upload_tasks,
    count(*) FILTER (WHERE projection.task_id IS NULL) AS missing_structured_rows,
    has_table_privilege(
        'cappa_rw',
        'public.publication_task_bilibili_uploads',
        'SELECT, INSERT, UPDATE, DELETE'
    ) AS cappa_rw_can_use_bilibili_uploads,
    to_regclass(
        'public.ix_publication_task_bili_uploads_parent_source_created'
    ) AS parent_source_index
FROM expected
LEFT JOIN public.publication_task_bilibili_uploads AS projection
    ON projection.task_id = expected.id;

COMMIT;
