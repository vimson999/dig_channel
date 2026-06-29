"""add report audio cover subtitle projection

Revision ID: 20260623_report_audio_cover_subtitle
Revises: 20260620_platform_credentials
Create Date: 2026-06-23

"""
from alembic import op
import sqlalchemy as sa


revision = "20260623_report_audio_cover_subtitle"
down_revision = "20260620_platform_credentials"
branch_labels = None
depends_on = None


def upgrade():
    _add_column_if_missing("publication_tasks", "report_audio_cover_subtitle", "varchar(220)")
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


def _add_column_if_missing(table_name, column_name, column_type):
    exists = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND column_name = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar()
    if exists:
        return
    op.execute(f"ALTER TABLE public.{table_name} ADD COLUMN {column_name} {column_type}")


def downgrade():
    op.execute(
        """
        ALTER TABLE public.publication_tasks
        DROP COLUMN IF EXISTS report_audio_cover_subtitle
        """
    )
