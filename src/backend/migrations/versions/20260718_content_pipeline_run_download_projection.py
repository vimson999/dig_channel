"""Add clean projections and a structured ZSXQ run-download ledger.

Revision ID: 20260718_clean_run_downloads
Revises: 20260718_bilibili_intelligence
"""

from alembic import op


revision = "20260718_clean_run_downloads"
down_revision = "20260718_bilibili_intelligence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS content_clean_output_dir varchar(600)")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS content_clean_files_count integer")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS content_clean_success_count integer")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS content_clean_failed_count integer")
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS content_clean_message text")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_parent_created_asc")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_source_created_asc")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_topics_group_tab_create_synced_desc")
    _create_bilibili_upload_projection()
    _backfill_bilibili_upload_projection()
    op.execute(
        "ALTER TABLE public.zsxq_sync_runs "
        "ADD COLUMN IF NOT EXISTS download_ledger_complete boolean NOT NULL DEFAULT false"
    )
    op.execute("""
        WITH source AS (
            SELECT id, COALESCE(result::jsonb, '{}'::jsonb) AS payload
            FROM public.publication_tasks
            WHERE feature IN ('content_pdf_clean', 'content_audio_clean')
        )
        UPDATE public.publication_tasks AS task
        SET
            content_clean_output_dir = NULLIF(source.payload ->> 'local_output_dir', ''),
            content_clean_files_count = CASE
                WHEN source.payload ->> 'files_count' ~ '^\\d+$'
                    THEN (source.payload ->> 'files_count')::integer
                WHEN jsonb_typeof(source.payload -> 'cleaned_files') = 'array'
                    THEN jsonb_array_length(source.payload -> 'cleaned_files')
                WHEN jsonb_typeof(source.payload -> 'files') = 'array'
                    THEN jsonb_array_length(source.payload -> 'files')
                ELSE 0
            END,
            content_clean_success_count = CASE
                WHEN source.payload ->> 'success_count' ~ '^\\d+$'
                    THEN (source.payload ->> 'success_count')::integer
                WHEN jsonb_typeof(source.payload -> 'cleaned_files') = 'array'
                    THEN jsonb_array_length(source.payload -> 'cleaned_files')
                ELSE 0
            END,
            content_clean_failed_count = CASE
                WHEN source.payload ->> 'failed_count' ~ '^\\d+$'
                    THEN (source.payload ->> 'failed_count')::integer
                ELSE 0
            END,
            content_clean_message = NULLIF(source.payload ->> 'message', '')
        FROM source
        WHERE task.id = source.id
    """)
    op.execute("""
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
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_zsxq_sync_run_downloads_run_ext
        ON public.zsxq_sync_run_downloads (run_id, file_ext)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_zsxq_sync_run_downloads_attachment
        ON public.zsxq_sync_run_downloads (attachment_id)
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON TABLE public.zsxq_sync_run_downloads TO cappa_rw
    """)
    _backfill_run_downloads()
    _backfill_download_ledger_complete()
    _compact_workflow_dispatch_summaries()
    op.execute("ANALYZE public.publication_tasks")
    op.execute("ANALYZE public.publication_task_bilibili_uploads")
    op.execute("ANALYZE public.zsxq_sync_run_downloads")
    op.execute("ANALYZE public.zsxq_workflow_runs")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.publication_task_bilibili_uploads")
    op.execute("DROP TABLE IF EXISTS public.zsxq_sync_run_downloads")
    op.execute("ALTER TABLE public.zsxq_sync_runs DROP COLUMN IF EXISTS download_ledger_complete")
    op.execute("ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS content_clean_message")
    op.execute("ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS content_clean_failed_count")
    op.execute("ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS content_clean_success_count")
    op.execute("ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS content_clean_files_count")
    op.execute("ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS content_clean_output_dir")


def _create_bilibili_upload_projection():
    op.execute("""
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
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_publication_task_bili_uploads_parent_source_created
        ON public.publication_task_bilibili_uploads (
            parent_task_id, parent_feature, source_label, created_at DESC
        )
    """)
    op.execute("""
        ALTER TABLE public.publication_task_bilibili_uploads
            ADD COLUMN IF NOT EXISTS queue_repair_count integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS queue_repair_last_at timestamp without time zone,
            ADD COLUMN IF NOT EXISTS queue_repair_previous_celery_id varchar(50),
            ADD COLUMN IF NOT EXISTS queue_repair_current_celery_id varchar(50),
            ADD COLUMN IF NOT EXISTS queue_repair_queue varchar(80),
            ADD COLUMN IF NOT EXISTS queue_repair_reason varchar(160),
            ADD COLUMN IF NOT EXISTS queue_repair_previous_message_visible boolean,
            ADD COLUMN IF NOT EXISTS canceled_at timestamp without time zone,
            ADD COLUMN IF NOT EXISTS cancel_reason varchar(160)
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON TABLE public.publication_task_bilibili_uploads TO cappa_rw
    """)


def _backfill_bilibili_upload_projection():
    op.execute(r"""
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
            source.created_at,
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
            updated_at = EXCLUDED.updated_at
    """)


def _backfill_run_downloads():
    op.execute(r"""
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
            downloaded_at = EXCLUDED.downloaded_at
    """)


def _compact_workflow_dispatch_summaries():
    for dispatch_key in ("auto_script_material_dispatch", "auto_video_dispatch"):
        op.execute(f"""
            WITH candidates AS (
                SELECT
                    id,
                    stage_results::jsonb AS payload,
                    stage_results::jsonb #> '{{sync,summary,{dispatch_key}}}' AS dispatch
                FROM public.zsxq_workflow_runs
                WHERE jsonb_typeof(
                    stage_results::jsonb #> '{{sync,summary,{dispatch_key}}}'
                ) = 'object'
            )
            UPDATE public.zsxq_workflow_runs AS workflow
            SET stage_results = jsonb_set(
                candidates.payload,
                '{{sync,summary,{dispatch_key}}}',
                (candidates.dispatch - 'config' - 'items')
                    || CASE
                        WHEN jsonb_typeof(candidates.dispatch -> 'pdf') = 'object'
                            THEN jsonb_build_object('pdf', (candidates.dispatch -> 'pdf') - 'items')
                        ELSE '{{}}'::jsonb
                    END
                    || CASE
                        WHEN jsonb_typeof(candidates.dispatch -> 'mp3') = 'object'
                            THEN jsonb_build_object('mp3', (candidates.dispatch -> 'mp3') - 'items')
                        ELSE '{{}}'::jsonb
                    END,
                false
            )
            FROM candidates
            WHERE workflow.id = candidates.id
        """)


def _backfill_download_ledger_complete():
    op.execute("""
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
        END
    """)
