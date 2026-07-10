"""add publisher and TTS child hot projections

Revision ID: 20260710_child_hot_projection
Revises: 20260702_report_video_render_previews
Create Date: 2026-07-10

"""
from alembic import op


revision = "20260710_child_hot_projection"
down_revision = "20260702_report_video_render_previews"
branch_labels = None
depends_on = None


def upgrade():
    for name, data_type in _COLUMNS:
        op.execute(
            f"ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS {name} {data_type}"
        )

    _backfill()
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_vertical_source_created "
        "ON public.publication_tasks (source_task_id, publisher_preview_seconds, created_at DESC) "
        "WHERE feature = 'publisher' AND publisher_vertical_enabled IS TRUE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_doubao_hash_completed_created "
        "ON public.publication_tasks (doubao_tts_text_hash, created_at DESC) "
        "WHERE feature = 'doubao_browser_tts' AND status = 'completed' "
        "AND doubao_tts_text_hash IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_doubao_parent_chunk_status "
        "ON public.publication_tasks (parent_task_id, doubao_tts_chunk_index, status, created_at DESC) "
        "WHERE feature = 'doubao_browser_tts' AND parent_task_id IS NOT NULL"
    )
    op.execute("ANALYZE public.publication_tasks")


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_doubao_parent_chunk_status")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_doubao_hash_completed_created")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_publisher_vertical_source_created")
    for name, _data_type in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS {name}")


_COLUMNS = (
    ("publisher_vertical_enabled", "boolean"),
    ("publisher_vertical_scheme", "varchar(32)"),
    ("publisher_preview_seconds", "integer NOT NULL DEFAULT 0"),
    ("publisher_vertical_video_path", "varchar(600)"),
    ("publisher_vertical_cover_path", "varchar(600)"),
    ("publisher_podcast_cover_path", "varchar(600)"),
    ("doubao_tts_text_hash", "varchar(64)"),
    ("doubao_tts_chunk_index", "integer NOT NULL DEFAULT 0"),
    ("doubao_tts_chunk_attempt", "integer NOT NULL DEFAULT 0"),
    ("doubao_tts_wav_path", "varchar(600)"),
)


def _backfill():
    op.execute(
        """
        UPDATE public.publication_tasks
        SET
            publisher_vertical_enabled = CASE
                WHEN feature = 'publisher' THEN lower(COALESCE(params #>> '{vertical_video,enabled}', 'false'))
                    IN ('1', 'true', 'yes', 'on')
                ELSE publisher_vertical_enabled
            END,
            publisher_vertical_scheme = LEFT(NULLIF(COALESCE(
                result #>> '{metadata,vertical_video_scheme}',
                result ->> 'vertical_video_scheme',
                params #>> '{vertical_video,scheme}'
            ), ''), 32),
            publisher_preview_seconds = CASE
                WHEN COALESCE(
                    result #>> '{metadata,preview_seconds}',
                    result ->> 'vertical_preview_seconds',
                    params #>> '{vertical_video,preview_seconds}'
                ) ~ '^[0-9]+([.][0-9]+)?$'
                THEN COALESCE(
                    result #>> '{metadata,preview_seconds}',
                    result ->> 'vertical_preview_seconds',
                    params #>> '{vertical_video,preview_seconds}'
                )::numeric::integer
                ELSE 0
            END,
            publisher_vertical_video_path = LEFT(NULLIF(COALESCE(
                result ->> 'vertical_video_path',
                CASE
                    WHEN lower(COALESCE(params #>> '{vertical_video,enabled}', 'false'))
                        IN ('1', 'true', 'yes', 'on')
                    THEN result ->> 'video_path'
                END
            ), ''), 600),
            publisher_vertical_cover_path = LEFT(NULLIF(result ->> 'vertical_cover_path', ''), 600),
            publisher_podcast_cover_path = LEFT(NULLIF(result ->> 'podcast_cover_path', ''), 600),
            doubao_tts_text_hash = LEFT(NULLIF(COALESCE(
                result #>> '{metadata,text_hash}',
                params #>> '{doubao_browser_tts,metadata,text_hash}'
            ), ''), 64),
            doubao_tts_chunk_index = CASE
                WHEN COALESCE(
                    result #>> '{metadata,chunk_index}',
                    params #>> '{doubao_browser_tts,metadata,chunk_index}'
                ) ~ '^[0-9]+$'
                THEN COALESCE(
                    result #>> '{metadata,chunk_index}',
                    params #>> '{doubao_browser_tts,metadata,chunk_index}'
                )::integer
                ELSE 0
            END,
            doubao_tts_chunk_attempt = CASE
                WHEN COALESCE(
                    result #>> '{metadata,chunk_attempt}',
                    params #>> '{doubao_browser_tts,metadata,chunk_attempt}'
                ) ~ '^[0-9]+$'
                THEN COALESCE(
                    result #>> '{metadata,chunk_attempt}',
                    params #>> '{doubao_browser_tts,metadata,chunk_attempt}'
                )::integer
                ELSE 0
            END,
            doubao_tts_wav_path = LEFT(NULLIF(COALESCE(
                result #>> '{audio,wav_path}',
                result ->> 'wav_path'
            ), ''), 600)
        WHERE feature IN ('publisher', 'doubao_browser_tts')
        """
    )
