"""add report audio cover subtitle projection

Revision ID: 20260623_report_audio_cover_subtitle
Revises: 20260620_platform_credentials
Create Date: 2026-06-23

"""
from alembic import op


revision = "20260623_report_audio_cover_subtitle"
down_revision = "20260620_platform_credentials"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE public.publication_tasks
        ADD COLUMN IF NOT EXISTS report_audio_cover_subtitle varchar(220)
        """
    )
    op.execute(
        """
        UPDATE public.publication_tasks
        SET report_audio_cover_subtitle = LEFT(NULLIF(COALESCE(
            result #>> '{structured_result,cover_subtitle}',
            result #>> '{video,cover_subtitle}'
        ), ''), 220)
        WHERE feature = 'report_pdf_audio_workflow'
          AND report_audio_cover_subtitle IS NULL
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE public.publication_tasks
        DROP COLUMN IF EXISTS report_audio_cover_subtitle
        """
    )
