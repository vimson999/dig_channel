"""add content pipeline web submission history projections

Revision ID: 20260912_web_submission_history
Revises: 20260902_zsxq_analysis_history
Create Date: 2026-09-12
"""

from alembic import op


revision = "20260912_web_submission_history"
down_revision = "20260902_zsxq_analysis_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS content_submission_channel varchar(40)"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS content_web_submission_id varchar(36)"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS content_submission_input_mode varchar(16)"
    )
    op.execute(
        """
        UPDATE public.publication_tasks
        SET
            content_submission_channel = params->'content_pipeline'->'metadata'->>'submission_channel',
            content_web_submission_id = params->'content_pipeline'->'metadata'->>'web_submission_id',
            content_submission_input_mode = params->'content_pipeline'->'metadata'->>'input_mode'
        WHERE feature IN ('content_pdf_clean', 'content_audio_clean')
          AND params->'content_pipeline'->'metadata'->>'submission_channel' = 'content_pipeline_web'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_web_submission_history "
        "ON public.publication_tasks (user_id, created_at DESC) "
        "WHERE content_submission_channel = 'content_pipeline_web' "
        "AND content_web_submission_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_web_submission_id "
        "ON public.publication_tasks (content_web_submission_id) "
        "WHERE content_web_submission_id IS NOT NULL"
    )
    op.execute(
        """
        UPDATE public.publication_tasks AS child
        SET user_id = parent.user_id
        FROM public.publication_tasks AS parent
        WHERE child.feature = 'zsxq_clean_output_publish'
          AND child.source_task_id = parent.id
          AND child.user_id IS NULL
          AND parent.feature IN ('content_pdf_clean', 'content_audio_clean')
          AND parent.user_id IS NOT NULL
        """
    )


def downgrade():
    op.execute(
        "DROP INDEX IF EXISTS public.ix_publication_tasks_web_submission_id"
    )
    op.execute(
        "DROP INDEX IF EXISTS public.ix_publication_tasks_web_submission_history"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS content_submission_input_mode"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS content_web_submission_id"
    )
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS content_submission_channel"
    )
