"""add selectable visual schemes to report storyboard workflows

Revision ID: 20260722_report_visual_schemes
Revises: 20260722_stick_audio_timeline
Create Date: 2026-07-22
"""
from alembic import op


revision = "20260722_report_visual_schemes"
down_revision = "20260722_stick_audio_timeline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE public.publication_tasks ADD COLUMN IF NOT EXISTS report_visual_scheme_key varchar(64)")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ADD COLUMN IF NOT EXISTS visual_scheme_key varchar(64)")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ADD COLUMN IF NOT EXISTS visual_scheme_name varchar(80)")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ADD COLUMN IF NOT EXISTS image_gemini_url varchar(600)")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ADD COLUMN IF NOT EXISTS image_style_profile varchar(80)")
    op.execute("UPDATE public.report_visual_plan_outputs SET visual_scheme_key='stick_figure' WHERE visual_scheme_key IS NULL")
    op.execute("UPDATE public.report_visual_plan_outputs SET visual_scheme_name='白板火柴人' WHERE visual_scheme_name IS NULL")
    op.execute("UPDATE public.report_visual_plan_outputs SET image_gemini_url='https://gemini.google.com/app' WHERE image_gemini_url IS NULL")
    op.execute("UPDATE public.report_visual_plan_outputs SET image_style_profile='whiteboard_stick_figure_zh_v1' WHERE image_style_profile IS NULL")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ALTER COLUMN visual_scheme_key SET NOT NULL")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ALTER COLUMN visual_scheme_name SET NOT NULL")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ALTER COLUMN image_gemini_url SET NOT NULL")
    op.execute("ALTER TABLE public.report_visual_plan_outputs ALTER COLUMN image_style_profile SET NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_visual_plan_scheme ON public.report_visual_plan_outputs (visual_scheme_key)")
    op.execute("""
        UPDATE public.publication_tasks
        SET report_visual_scheme_key='stick_figure'
        WHERE report_visual_scheme_key IS NULL
          AND feature IN ('report_stick_figure_video_workflow', 'report_visual_plan')
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_report_visual_plan_scheme")
    op.execute("ALTER TABLE public.report_visual_plan_outputs DROP COLUMN IF EXISTS image_style_profile")
    op.execute("ALTER TABLE public.report_visual_plan_outputs DROP COLUMN IF EXISTS image_gemini_url")
    op.execute("ALTER TABLE public.report_visual_plan_outputs DROP COLUMN IF EXISTS visual_scheme_name")
    op.execute("ALTER TABLE public.report_visual_plan_outputs DROP COLUMN IF EXISTS visual_scheme_key")
    op.execute("ALTER TABLE public.publication_tasks DROP COLUMN IF EXISTS report_visual_scheme_key")
