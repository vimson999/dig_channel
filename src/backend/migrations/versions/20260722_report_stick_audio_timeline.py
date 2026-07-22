"""add reusable whole-audio track and per-scene timeline

Revision ID: 20260722_stick_audio_timeline
Revises: 20260721_stick_scene_assets
Create Date: 2026-07-22
"""
from alembic import op


revision = "20260722_stick_audio_timeline"
down_revision = "20260721_stick_scene_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets ADD COLUMN IF NOT EXISTS timeline_start_seconds double precision")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets ADD COLUMN IF NOT EXISTS timeline_end_seconds double precision")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets ADD COLUMN IF NOT EXISTS timeline_confidence double precision")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets ADD COLUMN IF NOT EXISTS timeline_source varchar(32)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.report_stick_figure_audio_tracks (
            id varchar(36) PRIMARY KEY,
            workflow_task_id varchar(36) NOT NULL UNIQUE
                REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            storyboard_task_id varchar(36) NOT NULL
                REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            source_task_id varchar(36) NOT NULL
                REFERENCES public.publication_tasks(id) ON DELETE CASCADE,
            alignment_task_id varchar(36),
            mode varchar(32) NOT NULL DEFAULT 'whole_audio',
            status varchar(32) NOT NULL DEFAULT 'available',
            audio_path varchar(600) NOT NULL,
            audio_bytes bigint,
            audio_seconds double precision,
            source_script_hash varchar(64),
            storyboard_script_hash varchar(64),
            asr_cache_key varchar(128),
            asr_model varchar(40),
            aligned_scene_count integer NOT NULL DEFAULT 0,
            average_confidence double precision,
            minimum_confidence double precision,
            manifest_path varchar(600),
            error text,
            created_at timestamp without time zone NOT NULL DEFAULT now(),
            updated_at timestamp without time zone NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_audio_track_storyboard ON public.report_stick_figure_audio_tracks (storyboard_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_audio_track_source ON public.report_stick_figure_audio_tracks (source_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_audio_track_alignment_task ON public.report_stick_figure_audio_tracks (alignment_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_report_stick_audio_track_status ON public.report_stick_figure_audio_tracks (status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.report_stick_figure_audio_tracks")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets DROP COLUMN IF EXISTS timeline_source")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets DROP COLUMN IF EXISTS timeline_confidence")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets DROP COLUMN IF EXISTS timeline_end_seconds")
    op.execute("ALTER TABLE public.report_stick_figure_scene_assets DROP COLUMN IF EXISTS timeline_start_seconds")
