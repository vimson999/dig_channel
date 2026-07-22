-- Complete the Bilibili upload execution projection discovered by the
-- post-migration runtime audit. Run the whole file as postgres/admin.
-- Stop Web, Celery, schedulers and local PgBouncer first. Keep auto-commit on;
-- this explicit transaction is all-or-nothing and contains no CONCURRENTLY.

BEGIN;

SET LOCAL lock_timeout = '60s';
SET LOCAL statement_timeout = '10min';

DO $migration_guard$
DECLARE app_connections integer;
BEGIN
    SELECT count(*)
    INTO app_connections
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND pid <> pg_backend_pid()
      AND usename = 'cappa_rw';

    IF app_connections > 0 THEN
        RAISE EXCEPTION
            'Refusing online DDL: % cappa_rw connection(s) still attached. Stop Web/Celery/schedulers/PgBouncer first.',
            app_connections;
    END IF;
END
$migration_guard$;

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
    ADD COLUMN IF NOT EXISTS creation_statement_id integer;

ALTER TABLE public.publication_tasks
    ADD COLUMN IF NOT EXISTS report_audio_vertical_cover_path varchar(600),
    ADD COLUMN IF NOT EXISTS report_audio_podcast_cover_path varchar(600),
    ADD COLUMN IF NOT EXISTS report_audio_bilibili_cover_path varchar(600),
    ADD COLUMN IF NOT EXISTS publisher_bilibili_cover_path varchar(600);

-- JSON is opened once for historical recovery. New writes populate these
-- typed columns in the same transaction as the task and never need this path.
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
WHERE upload.task_id = source.id;

-- Cover download endpoints use these bounded paths instead of reopening the
-- parent/child result JSON for every browser image request.
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
WHERE feature IN ('report_pdf_audio_workflow', 'report_audio_workflow');

UPDATE public.publication_tasks
SET publisher_bilibili_cover_path = LEFT(
    NULLIF(result::jsonb ->> 'bilibili_cover_path', ''),
    600
)
WHERE feature = 'publisher';

DO $validation$
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
$validation$;

DO $media_validation$
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
$media_validation$;

ALTER TABLE public.publication_task_bilibili_uploads
    ALTER COLUMN video_path SET NOT NULL,
    ALTER COLUMN title SET NOT NULL,
    ALTER COLUMN neutral_mark SET DEFAULT '个人观点，仅供参考',
    ALTER COLUMN neutral_mark SET NOT NULL;

GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.publication_task_bilibili_uploads TO cappa_rw;

ANALYZE public.publication_task_bilibili_uploads;
ANALYZE public.publication_tasks;

-- All four counts must equal total_uploads. Missing rows must be zero.
SELECT
    count(*) AS total_uploads,
    count(*) FILTER (WHERE video_path IS NOT NULL) AS with_video_path,
    count(*) FILTER (WHERE title IS NOT NULL) AS with_title,
    count(*) FILTER (WHERE cardinality(tags) > 0) AS with_tags,
    count(*) FILTER (
        WHERE video_path IS NULL
           OR title IS NULL
           OR cardinality(tags) = 0
           OR neutral_mark IS NULL
    ) AS missing_execution_projection
FROM public.publication_task_bilibili_uploads;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'publication_task_bilibili_uploads'
  AND column_name IN (
      'video_path', 'cover_path', 'description', 'tags', 'tid', 'copyright',
      'no_reprint', 'dynamic', 'neutral_mark', 'is_only_self',
      'space_hidden', 'creation_statement_id'
  )
ORDER BY column_name;

-- All four values must be zero.
SELECT
    count(*) FILTER (
        WHERE feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
          AND report_audio_vertical_cover_path IS DISTINCT FROM LEFT(COALESCE(
              NULLIF(result::jsonb #>> '{video,vertical_cover_path}', ''),
              NULLIF(result::jsonb #>> '{video,keyframes,vertical_cover,path}', '')
          ), 600)
    ) AS missing_vertical_cover_projection,
    count(*) FILTER (
        WHERE feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
          AND report_audio_podcast_cover_path IS DISTINCT FROM LEFT(COALESCE(
              NULLIF(result::jsonb #>> '{video,podcast_cover_path}', ''),
              NULLIF(result::jsonb #>> '{video,keyframes,podcast_cover,path}', '')
          ), 600)
    ) AS missing_podcast_cover_projection,
    count(*) FILTER (
        WHERE feature IN ('report_pdf_audio_workflow', 'report_audio_workflow')
          AND report_audio_bilibili_cover_path IS DISTINCT FROM LEFT(COALESCE(
              NULLIF(result::jsonb #>> '{video,bilibili_cover_path}', ''),
              NULLIF(result::jsonb #>> '{video,keyframes,bilibili_cover,path}', '')
          ), 600)
    ) AS missing_bilibili_cover_projection,
    count(*) FILTER (
        WHERE feature = 'publisher'
          AND publisher_bilibili_cover_path IS DISTINCT FROM LEFT(
              NULLIF(result::jsonb ->> 'bilibili_cover_path', ''),
              600
          )
    ) AS missing_publisher_bilibili_cover_projection
FROM public.publication_tasks;

COMMIT;
