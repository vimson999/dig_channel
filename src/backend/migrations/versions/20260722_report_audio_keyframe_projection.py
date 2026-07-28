"""add report audio landscape keyframe projection

Revision ID: 20260722_report_audio_keyframe
Revises: 20260722_report_visual_schemes
Create Date: 2026-07-22
"""
from alembic import op


revision = "20260722_report_audio_keyframe"
down_revision = "20260722_report_visual_schemes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '5min'")
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "ADD COLUMN IF NOT EXISTS report_audio_landscape_keyframe_path varchar(600)"
    )
    op.execute("""
        UPDATE public.publication_tasks
        SET report_audio_landscape_keyframe_path = LEFT(
            NULLIF(result::jsonb #>> '{video,keyframes,landscape_cover,path}', ''),
            600
        )
        WHERE feature = 'report_pdf_audio_workflow'
          AND report_audio_landscape_keyframe_path IS NULL
          AND NULLIF(result::jsonb #>> '{video,keyframes,landscape_cover,path}', '') IS NOT NULL
    """)


def downgrade():
    op.execute(
        "ALTER TABLE public.publication_tasks "
        "DROP COLUMN IF EXISTS report_audio_landscape_keyframe_path"
    )
