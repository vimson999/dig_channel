"""add Gemini response length projection

Revision ID: 20260710_gemini_chars
Revises: 20260710_child_hot_projection
Create Date: 2026-07-10

"""
from alembic import op


revision = "20260710_gemini_chars"
down_revision = "20260710_child_hot_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS gemini_response_chars integer NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS publisher_landscape_enabled boolean"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS publisher_render_mode varchar(32)"
    )
    op.execute(
        """
        UPDATE public.publication_tasks
        SET gemini_response_chars = length(regexp_replace(
            COALESCE(result ->> 'final_response_text', result ->> 'response_text', ''),
            '\\s+',
            '',
            'g'
        ))
        WHERE feature IN ('report_pdf_gemini', 'gemini_browser')
          AND gemini_response_chars = 0
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_gemini_parent_status_created "
        "ON public.publication_tasks (parent_task_id, status, created_at DESC) "
        "WHERE feature IN ('report_pdf_gemini', 'gemini_browser')"
    )
    op.execute(
        """
        UPDATE public.publication_tasks
        SET
            publisher_landscape_enabled = lower(COALESCE(params #>> '{video,enabled}', 'false'))
                IN ('1', 'true', 'yes', 'on'),
            publisher_render_mode = LEFT(COALESCE(
                NULLIF(result #>> '{metadata,render_mode}', ''),
                NULLIF(result ->> 'render_mode', ''),
                NULLIF(params #>> '{metadata,render_mode}', ''),
                NULLIF(params ->> 'render_mode', ''),
                'full_video'
            ), 32)
        WHERE feature = 'publisher'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_publisher_source_mode_status_created "
        "ON public.publication_tasks "
        "(source_task_id, publisher_render_mode, publisher_preview_seconds, status, created_at DESC) "
        "WHERE feature = 'publisher' AND source_task_id IS NOT NULL"
    )
    op.execute("ANALYZE public.publication_tasks")


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_publisher_source_mode_status_created")
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_gemini_parent_status_created")
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS publisher_render_mode"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS publisher_landscape_enabled"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS gemini_response_chars"
    )
