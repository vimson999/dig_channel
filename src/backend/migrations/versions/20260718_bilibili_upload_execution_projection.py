"""Store the complete Bilibili upload command outside task JSON.

Revision ID: 20260718_bili_upload_exec
Revises: 20260718_clean_run_downloads
"""

from alembic import op


revision = "20260718_bili_upload_exec"
down_revision = "20260718_clean_run_downloads"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.publication_task_bilibili_uploads
            ADD COLUMN IF NOT EXISTS video_path varchar(600),
            ADD COLUMN IF NOT EXISTS cover_path varchar(600),
            ADD COLUMN IF NOT EXISTS description text,
            ADD COLUMN IF NOT EXISTS tags varchar(20)[] NOT NULL DEFAULT ARRAY[]::varchar(20)[],
            ADD COLUMN IF NOT EXISTS tid integer NOT NULL DEFAULT 207,
            ADD COLUMN IF NOT EXISTS copyright integer NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS no_reprint integer NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS dynamic varchar(233),
            ADD COLUMN IF NOT EXISTS neutral_mark varchar(80),
            ADD COLUMN IF NOT EXISTS is_only_self integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS space_hidden integer,
            ADD COLUMN IF NOT EXISTS creation_statement_id integer
    """)
    op.execute("""
        ALTER TABLE public.publication_tasks
            ADD COLUMN IF NOT EXISTS report_audio_vertical_cover_path varchar(600),
            ADD COLUMN IF NOT EXISTS report_audio_podcast_cover_path varchar(600),
            ADD COLUMN IF NOT EXISTS report_audio_bilibili_cover_path varchar(600),
            ADD COLUMN IF NOT EXISTS publisher_bilibili_cover_path varchar(600)
    """)
    _backfill_execution_fields()
    _backfill_media_projection_fields()
    op.execute("""
        ALTER TABLE public.publication_task_bilibili_uploads
            ALTER COLUMN video_path SET NOT NULL,
            ALTER COLUMN title SET NOT NULL,
            ALTER COLUMN neutral_mark SET DEFAULT '个人观点，仅供参考',
            ALTER COLUMN neutral_mark SET NOT NULL
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON TABLE public.publication_task_bilibili_uploads TO cappa_rw
    """)
    op.execute("ANALYZE public.publication_task_bilibili_uploads")
    op.execute("ANALYZE public.publication_tasks")


def downgrade():
    op.execute("""
        ALTER TABLE public.publication_tasks
            DROP COLUMN IF EXISTS publisher_bilibili_cover_path,
            DROP COLUMN IF EXISTS report_audio_bilibili_cover_path,
            DROP COLUMN IF EXISTS report_audio_podcast_cover_path,
            DROP COLUMN IF EXISTS report_audio_vertical_cover_path
    """)
    op.execute("""
        ALTER TABLE public.publication_task_bilibili_uploads
            DROP COLUMN IF EXISTS creation_statement_id,
            DROP COLUMN IF EXISTS space_hidden,
            DROP COLUMN IF EXISTS is_only_self,
            DROP COLUMN IF EXISTS neutral_mark,
            DROP COLUMN IF EXISTS dynamic,
            DROP COLUMN IF EXISTS no_reprint,
            DROP COLUMN IF EXISTS copyright,
            DROP COLUMN IF EXISTS tid,
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS cover_path,
            DROP COLUMN IF EXISTS video_path
    """)


def _backfill_execution_fields():
    op.execute(r"""
        WITH source AS (
            SELECT
                task.id,
                COALESCE(task.params::jsonb, '{}'::jsonb) AS params_payload,
                COALESCE(task.result::jsonb, '{}'::jsonb) AS result_payload
            FROM public.publication_tasks AS task
            JOIN public.publication_task_bilibili_uploads AS existing
              ON existing.task_id = task.id
        )
        UPDATE public.publication_task_bilibili_uploads AS upload
        SET
            video_path = COALESCE(
                NULLIF(source.result_payload ->> 'video_path', ''),
                NULLIF(source.params_payload ->> 'upload_video_path', ''),
                NULLIF(source.params_payload #>> '{video,video_path}', '')
            ),
            cover_path = COALESCE(
                NULLIF(source.params_payload #>> '{bilibili,cover_path}', ''),
                NULLIF(source.result_payload ->> 'cover_path', '')
            ),
            description = NULLIF(source.params_payload #>> '{bilibili,desc}', ''),
            tags = CASE
                WHEN jsonb_typeof(source.params_payload #> '{bilibili,tags}') = 'array'
                THEN COALESCE(
                    NULLIF(
                        ARRAY(
                            SELECT LEFT(BTRIM(tag.value), 20)::varchar(20)
                            FROM jsonb_array_elements_text(
                                source.params_payload #> '{bilibili,tags}'
                            ) WITH ORDINALITY AS tag(value, position)
                            WHERE BTRIM(tag.value) <> ''
                            ORDER BY tag.position
                            LIMIT 10
                        ),
                        ARRAY[]::varchar(20)[]
                    ),
                    ARRAY['财经', '音频', '视频']::varchar(20)[]
                )
                ELSE ARRAY['财经', '音频', '视频']::varchar(20)[]
            END,
            tid = CASE
                WHEN source.params_payload #>> '{bilibili,tid}' ~ '^\d+$'
                    THEN (source.params_payload #>> '{bilibili,tid}')::integer
                ELSE 207
            END,
            copyright = CASE
                WHEN source.params_payload #>> '{bilibili,copyright}' ~ '^\d+$'
                    THEN (source.params_payload #>> '{bilibili,copyright}')::integer
                ELSE 1
            END,
            no_reprint = CASE
                WHEN source.params_payload #>> '{bilibili,no_reprint}' ~ '^\d+$'
                    THEN (source.params_payload #>> '{bilibili,no_reprint}')::integer
                ELSE 1
            END,
            dynamic = LEFT(NULLIF(source.params_payload #>> '{bilibili,dynamic}', ''), 233),
            neutral_mark = LEFT(COALESCE(
                NULLIF(source.params_payload #>> '{bilibili,neutral_mark}', ''),
                '个人观点，仅供参考'
            ), 80),
            is_only_self = CASE
                WHEN COALESCE(
                    source.params_payload #>> '{bilibili,is_only_self}',
                    source.params_payload #>> '{bilibili,isOnlySelf}',
                    source.params_payload #>> '{bilibili,only_self}'
                ) ~ '^\d+$'
                THEN COALESCE(
                    source.params_payload #>> '{bilibili,is_only_self}',
                    source.params_payload #>> '{bilibili,isOnlySelf}',
                    source.params_payload #>> '{bilibili,only_self}'
                )::integer
                ELSE 0
            END,
            space_hidden = CASE
                WHEN source.params_payload #>> '{bilibili,space_hidden}' ~ '^-?\d+$'
                    THEN (source.params_payload #>> '{bilibili,space_hidden}')::integer
                ELSE NULL
            END,
            creation_statement_id = CASE
                WHEN COALESCE(
                    source.params_payload #>> '{bilibili,creation_statement_id}',
                    source.params_payload #>> '{bilibili,creationStatementId}'
                ) ~ '^\d+$'
                THEN COALESCE(
                    source.params_payload #>> '{bilibili,creation_statement_id}',
                    source.params_payload #>> '{bilibili,creationStatementId}'
                )::integer
                ELSE NULL
            END,
            updated_at = now()
        FROM source
        WHERE upload.task_id = source.id
    """)
    op.execute("""
        DO $execution_validation$
        DECLARE incomplete_rows integer;
        BEGIN
            SELECT count(*)
            INTO incomplete_rows
            FROM public.publication_task_bilibili_uploads
            WHERE video_path IS NULL
               OR title IS NULL
               OR cardinality(tags) = 0
               OR neutral_mark IS NULL;
            IF incomplete_rows > 0 THEN
                RAISE EXCEPTION
                    'Bilibili execution projection backfill incomplete: % row(s)',
                    incomplete_rows;
            END IF;
        END
        $execution_validation$
    """)


def _backfill_media_projection_fields():
    op.execute("""
        UPDATE public.publication_tasks
        SET
            report_audio_vertical_cover_path = LEFT(COALESCE(
                NULLIF(result::jsonb #>> '{video,vertical_cover_path}', ''),
                NULLIF(result::jsonb #>> '{video,keyframes,vertical_cover,path}', '')
            ), 600),
            report_audio_podcast_cover_path = LEFT(COALESCE(
                NULLIF(result::jsonb #>> '{video,podcast_cover_path}', ''),
                NULLIF(result::jsonb #>> '{video,keyframes,podcast_cover,path}', '')
            ), 600),
            report_audio_bilibili_cover_path = LEFT(COALESCE(
                NULLIF(result::jsonb #>> '{video,bilibili_cover_path}', ''),
                NULLIF(result::jsonb #>> '{video,keyframes,bilibili_cover,path}', '')
            ), 600)
        WHERE feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
    """)
    op.execute("""
        UPDATE public.publication_tasks
        SET publisher_bilibili_cover_path = LEFT(
            NULLIF(result::jsonb ->> 'bilibili_cover_path', ''),
            600
        )
        WHERE feature = 'publisher'
    """)
    op.execute("""
        DO $validation$
        DECLARE incomplete_rows integer;
        BEGIN
            SELECT count(*)
            INTO incomplete_rows
            FROM public.publication_tasks
            WHERE (
                    feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
                    AND (
                        report_audio_vertical_cover_path IS DISTINCT FROM LEFT(COALESCE(
                            NULLIF(result::jsonb #>> '{video,vertical_cover_path}', ''),
                            NULLIF(result::jsonb #>> '{video,keyframes,vertical_cover,path}', '')
                        ), 600)
                        OR report_audio_podcast_cover_path IS DISTINCT FROM LEFT(COALESCE(
                            NULLIF(result::jsonb #>> '{video,podcast_cover_path}', ''),
                            NULLIF(result::jsonb #>> '{video,keyframes,podcast_cover,path}', '')
                        ), 600)
                        OR report_audio_bilibili_cover_path IS DISTINCT FROM LEFT(COALESCE(
                            NULLIF(result::jsonb #>> '{video,bilibili_cover_path}', ''),
                            NULLIF(result::jsonb #>> '{video,keyframes,bilibili_cover,path}', '')
                        ), 600)
                    )
                )
                OR (
                    feature = 'publisher'
                    AND publisher_bilibili_cover_path IS DISTINCT FROM LEFT(
                        NULLIF(result::jsonb ->> 'bilibili_cover_path', ''),
                        600
                    )
                );
            IF incomplete_rows > 0 THEN
                RAISE EXCEPTION
                    'Media cover projection backfill incomplete: % row(s)',
                    incomplete_rows;
            END IF;
        END
        $validation$
    """)
