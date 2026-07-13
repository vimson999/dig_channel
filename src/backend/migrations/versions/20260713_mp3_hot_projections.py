"""add MP3 list and publisher match projections

Revision ID: 20260713_mp3_hot_projection
Revises: 20260710_gemini_chars
Create Date: 2026-07-13

"""
from alembic import op


revision = "20260713_mp3_hot_projection"
down_revision = "20260710_gemini_chars"
branch_labels = None
depends_on = None


def upgrade():
    for name, data_type in _COLUMNS:
        op.execute(
            f"ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS {name} {data_type}"
        )
    _backfill()
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_mp3_match "
        "ON public.publication_tasks "
        "(source_task_id, publisher_child_role, publisher_aspect_ratio, "
        "publisher_generate_subtitles, publisher_audio_signature, status, created_at DESC) "
        "WHERE feature = 'publisher' AND publisher_child_role IS NOT NULL"
    )
    op.execute("ANALYZE public.publication_tasks")


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_publisher_mp3_match")
    for name, _data_type in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS {name}")


_COLUMNS = (
    ("publisher_child_role", "varchar(80)"),
    ("publisher_aspect_ratio", "varchar(16)"),
    ("publisher_generate_subtitles", "boolean"),
    ("publisher_audio_signature", "varchar(64)"),
    ("mp3_audio_filename", "varchar(500)"),
    ("mp3_audio_topic_title", "varchar(500)"),
    ("mp3_audio_tab_label", "varchar(160)"),
    ("mp3_audio_path", "varchar(600)"),
    ("mp3_audio_download_count", "integer"),
    ("mp3_audio_duration_seconds", "double precision"),
)


def _backfill():
    op.execute(
        """
        UPDATE public.publication_tasks
        SET
            source_task_id = LEFT(NULLIF(COALESCE(
                source_task_id,
                parent_task_id,
                params ->> 'source_task_id',
                params ->> 'parent_task_id'
            ), ''), 36),
            publisher_child_role = LEFT(NULLIF(COALESCE(
                params #>> '{metadata,role}', result #>> '{metadata,role}'
            ), ''), 80),
            publisher_aspect_ratio = LEFT(NULLIF(COALESCE(
                params ->> 'aspect_ratio', result ->> 'aspect_ratio'
            ), ''), 16),
            publisher_generate_subtitles = CASE
                WHEN lower(COALESCE(
                    params ->> 'generate_subtitles',
                    params #>> '{metadata,generate_subtitles}',
                    params #>> '{video,sub_burn_enabled}',
                    result ->> 'generate_subtitles',
                    'true'
                )) IN ('1', 'true', 'yes', 'on') THEN TRUE
                ELSE FALSE
            END,
            publisher_audio_signature = LEFT(NULLIF(COALESCE(
                params #>> '{metadata,audio_signature}',
                result #>> '{metadata,audio_signature}',
                result ->> 'audio_signature'
            ), ''), 64)
        WHERE feature = 'publisher'
        """
    )
    op.execute(
        """
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
                params #>> '{mp3_video_workflow,audio,filename}',
                params #>> '{mp3_video_workflow,audio,name}'
            ), ''), 500),
            mp3_audio_topic_title = LEFT(NULLIF(COALESCE(
                result #>> '{audio,topic_title}', result #>> '{audio,title}',
                params #>> '{mp3_video_workflow,audio,topic_title}',
                params #>> '{mp3_video_workflow,audio,title}'
            ), ''), 500),
            mp3_audio_tab_label = LEFT(NULLIF(COALESCE(
                result #>> '{audio,tab_label}', result #>> '{audio,tab}',
                params #>> '{mp3_video_workflow,audio,tab_label}',
                params #>> '{mp3_video_workflow,audio,tab}'
            ), ''), 160),
            mp3_audio_path = LEFT(NULLIF(COALESCE(
                result #>> '{audio,path}', params #>> '{mp3_video_workflow,audio,path}'
            ), ''), 600),
            mp3_audio_download_count = CASE
                WHEN COALESCE(
                    result #>> '{audio,download_count}',
                    params #>> '{mp3_video_workflow,audio,download_count}'
                ) ~ '^[0-9]+$'
                THEN COALESCE(
                    result #>> '{audio,download_count}',
                    params #>> '{mp3_video_workflow,audio,download_count}'
                )::integer
                ELSE NULL
            END,
            mp3_audio_duration_seconds = CASE
                WHEN COALESCE(
                    result #>> '{audio,duration_seconds}',
                    params #>> '{mp3_video_workflow,audio,duration_seconds}'
                ) ~ '^[0-9]+([.][0-9]+)?$'
                THEN COALESCE(
                    result #>> '{audio,duration_seconds}',
                    params #>> '{mp3_video_workflow,audio,duration_seconds}'
                )::double precision
                ELSE NULL
            END
        WHERE feature = 'mp3_video_workflow'
        """
    )
