"""add per-scene image and tts asset state

Revision ID: 20260721_stick_scene_assets
Revises: 20260721_visual_plan_segments
Create Date: 2026-07-21
"""
from alembic import op


revision = "20260721_stick_scene_assets"
down_revision = "20260721_visual_plan_segments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.report_stick_figure_scene_assets (
            id varchar(36) PRIMARY KEY,
            workflow_task_id varchar(36) NOT NULL REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            storyboard_task_id varchar(36) NOT NULL REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            segment_id varchar(36) NOT NULL REFERENCES public.report_visual_plan_segments(id) ON DELETE CASCADE,
            sequence integer NOT NULL,
            script_text_hash varchar(64) NOT NULL,
            visual_prompt_hash varchar(64) NOT NULL,
            image_task_id varchar(36),
            image_status varchar(32) NOT NULL DEFAULT 'pending',
            image_progress integer NOT NULL DEFAULT 0,
            image_attempt integer NOT NULL DEFAULT 0,
            image_path varchar(600),
            image_bytes bigint,
            image_width integer,
            image_height integer,
            image_error text,
            tts_task_id varchar(36),
            tts_status varchar(32) NOT NULL DEFAULT 'pending',
            tts_progress integer NOT NULL DEFAULT 0,
            tts_attempt integer NOT NULL DEFAULT 0,
            audio_path varchar(600),
            audio_bytes bigint,
            audio_seconds double precision,
            tts_error text,
            created_at timestamp without time zone NOT NULL DEFAULT now(),
            updated_at timestamp without time zone NOT NULL DEFAULT now(),
            CONSTRAINT uq_report_stick_scene_workflow_storyboard_sequence
                UNIQUE (workflow_task_id, storyboard_task_id, sequence)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_scene_workflow_task_id ON public.report_stick_figure_scene_assets (workflow_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_scene_storyboard_task_id ON public.report_stick_figure_scene_assets (storyboard_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_scene_segment_id ON public.report_stick_figure_scene_assets (segment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_scene_image_task_id ON public.report_stick_figure_scene_assets (image_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_scene_tts_task_id ON public.report_stick_figure_scene_assets (tts_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_scene_workflow_sequence ON public.report_stick_figure_scene_assets (workflow_task_id, sequence)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.report_stick_figure_scene_assets")
