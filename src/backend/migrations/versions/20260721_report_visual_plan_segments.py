"""add structured report visual plan outputs and segments

Revision ID: 20260721_visual_plan_segments
Revises: 20260718_bili_upload_exec
Create Date: 2026-07-21
"""
from alembic import op


revision = "20260721_visual_plan_segments"
down_revision = "20260718_bili_upload_exec"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.report_visual_plan_outputs (
            task_id varchar(36) PRIMARY KEY REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            source_task_id varchar(36) NOT NULL,
            schema_version varchar(64) NOT NULL,
            source_script_hash varchar(64) NOT NULL,
            source_script_chars integer NOT NULL DEFAULT 0,
            covered_script_chars integer NOT NULL DEFAULT 0,
            segment_count integer NOT NULL DEFAULT 0,
            gemini_url varchar(600) NOT NULL,
            gemini_output_path varchar(600),
            normalized_output_path varchar(600),
            normalizer_provider varchar(40),
            normalizer_model varchar(120),
            created_at timestamp without time zone NOT NULL DEFAULT now(),
            updated_at timestamp without time zone NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.report_visual_plan_segments (
            id varchar(36) PRIMARY KEY,
            task_id varchar(36) NOT NULL REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            sequence integer NOT NULL,
            scene_title varchar(240),
            script_text text NOT NULL,
            visual_prompt text NOT NULL,
            estimated_seconds double precision,
            created_at timestamp without time zone NOT NULL DEFAULT now(),
            CONSTRAINT uq_report_visual_plan_segment_task_sequence UNIQUE (task_id, sequence)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_visual_plan_outputs_source_task_id ON public.report_visual_plan_outputs (source_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_visual_plan_outputs_source_script_hash ON public.report_visual_plan_outputs (source_script_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_visual_plan_segments_task_sequence ON public.report_visual_plan_segments (task_id, sequence)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.report_visual_plan_segments")
    op.execute("DROP TABLE IF EXISTS public.report_visual_plan_outputs")
