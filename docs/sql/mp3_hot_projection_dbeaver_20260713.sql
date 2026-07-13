-- MP3 workflow hot projections. Run once in DBeaver as postgres/admin.
-- Keep auto-commit enabled. This script preserves params/result in full.

-- ALTER TABLE needs a brief ACCESS EXCLUSIVE lock. Run this after stopping
-- the local Web/worker stack; 30s remains a bounded safety guard.
SET lock_timeout = '30s';
SET statement_timeout = '10min';

GRANT CREATE ON SCHEMA public TO cappa_rw;
SET ROLE cappa_rw;

ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS publisher_child_role varchar(80);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS publisher_aspect_ratio varchar(16);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS publisher_generate_subtitles boolean;
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS publisher_audio_signature varchar(64);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS mp3_audio_filename varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS mp3_audio_topic_title varchar(500);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS mp3_audio_tab_label varchar(160);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS mp3_audio_path varchar(600);
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS mp3_audio_download_count integer;
ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS mp3_audio_duration_seconds double precision;

UPDATE public.publication_tasks
SET
    source_task_id = LEFT(NULLIF(COALESCE(
        source_task_id, parent_task_id, params ->> 'source_task_id', params ->> 'parent_task_id'
    ), ''), 36),
    publisher_child_role = LEFT(NULLIF(COALESCE(params #>> '{metadata,role}', result #>> '{metadata,role}'), ''), 80),
    publisher_aspect_ratio = LEFT(NULLIF(COALESCE(params ->> 'aspect_ratio', result ->> 'aspect_ratio'), ''), 16),
    publisher_generate_subtitles = CASE
        WHEN lower(COALESCE(
            params ->> 'generate_subtitles', params #>> '{metadata,generate_subtitles}',
            params #>> '{video,sub_burn_enabled}', result ->> 'generate_subtitles', 'true'
        )) IN ('1', 'true', 'yes', 'on') THEN TRUE ELSE FALSE
    END,
    publisher_audio_signature = LEFT(NULLIF(COALESCE(
        params #>> '{metadata,audio_signature}', result #>> '{metadata,audio_signature}',
        result ->> 'audio_signature'
    ), ''), 64)
WHERE feature = 'publisher';

UPDATE public.publication_tasks
SET
    source_task_id = LEFT(NULLIF(COALESCE(
        source_task_id,
        params #>> '{mp3_video_workflow,audio_asset_id}',
        params #>> '{mp3_video_workflow,audio,attachment_id}',
        params #>> '{mp3_video_workflow,audio,audio_asset_id}'
    ), ''), 36),
    mp3_audio_filename = LEFT(NULLIF(COALESCE(
        result #>> '{audio,filename}', result #>> '{audio,name}',
        params #>> '{mp3_video_workflow,audio,filename}', params #>> '{mp3_video_workflow,audio,name}'
    ), ''), 500),
    mp3_audio_topic_title = LEFT(NULLIF(COALESCE(
        result #>> '{audio,topic_title}', result #>> '{audio,title}',
        params #>> '{mp3_video_workflow,audio,topic_title}', params #>> '{mp3_video_workflow,audio,title}'
    ), ''), 500),
    mp3_audio_tab_label = LEFT(NULLIF(COALESCE(
        result #>> '{audio,tab_label}', result #>> '{audio,tab}',
        params #>> '{mp3_video_workflow,audio,tab_label}', params #>> '{mp3_video_workflow,audio,tab}'
    ), ''), 160),
    mp3_audio_path = LEFT(NULLIF(COALESCE(
        result #>> '{audio,path}', params #>> '{mp3_video_workflow,audio,path}'
    ), ''), 600),
    mp3_audio_download_count = CASE
        WHEN COALESCE(result #>> '{audio,download_count}', params #>> '{mp3_video_workflow,audio,download_count}') ~ '^[0-9]+$'
        THEN COALESCE(result #>> '{audio,download_count}', params #>> '{mp3_video_workflow,audio,download_count}')::integer
        ELSE NULL
    END,
    mp3_audio_duration_seconds = CASE
        WHEN COALESCE(result #>> '{audio,duration_seconds}', params #>> '{mp3_video_workflow,audio,duration_seconds}') ~ '^[0-9]+([.][0-9]+)?$'
        THEN COALESCE(result #>> '{audio,duration_seconds}', params #>> '{mp3_video_workflow,audio,duration_seconds}')::double precision
        ELSE NULL
    END
WHERE feature = 'mp3_video_workflow';

CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_mp3_match
    ON public.publication_tasks (
        source_task_id,
        publisher_child_role,
        publisher_aspect_ratio,
        publisher_generate_subtitles,
        publisher_audio_signature,
        status,
        created_at DESC
    )
    WHERE feature = 'publisher' AND publisher_child_role IS NOT NULL;

ANALYZE public.publication_tasks;
RESET ROLE;

SELECT
    count(*) FILTER (WHERE feature = 'mp3_video_workflow') AS total_mp3_tasks,
    count(*) FILTER (WHERE feature = 'mp3_video_workflow' AND mp3_audio_filename IS NOT NULL) AS with_audio_filename,
    count(*) FILTER (WHERE feature = 'mp3_video_workflow' AND mp3_audio_path IS NOT NULL) AS with_audio_path,
    count(*) FILTER (WHERE feature = 'publisher' AND publisher_child_role IS NOT NULL) AS with_publisher_role,
    count(*) FILTER (WHERE feature = 'publisher' AND publisher_audio_signature IS NOT NULL) AS with_audio_signature
FROM public.publication_tasks;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname = 'ix_publication_tasks_publisher_mp3_match';
