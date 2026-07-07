"""add report video render previews

Revision ID: 20260702_report_video_render_previews
Revises: 20260629_wide_projection_phase1
Create Date: 2026-07-02

"""
from alembic import op


revision = "20260702_report_video_render_previews"
down_revision = "20260629_wide_projection_phase1"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.report_video_render_previews (
            id varchar(36) PRIMARY KEY,
            task_id varchar(36) NOT NULL,
            report_id varchar(36) NULL,
            preview_type varchar(30) NOT NULL,
            ratio varchar(10) NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'pending',
            duration_sec double precision NULL,
            file_path varchar(600) NULL,
            file_paths jsonb NULL,
            segment_ids jsonb NULL,
            renderer_version varchar(80) NULL,
            celery_task_id varchar(80) NULL,
            meta jsonb NULL,
            approved_at timestamp NULL,
            rejected_at timestamp NULL,
            reject_reason text NULL,
            error_message text NULL,
            created_at timestamp NOT NULL,
            updated_at timestamp NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_report_video_render_previews_task_type_ratio
        ON public.report_video_render_previews (task_id, preview_type, ratio, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_report_video_render_previews_status
        ON public.report_video_render_previews (status, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_report_video_render_previews_celery
        ON public.report_video_render_previews (celery_task_id)
        WHERE celery_task_id IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_report_video_render_previews_celery")
    op.execute("DROP INDEX IF EXISTS public.ix_report_video_render_previews_status")
    op.execute("DROP INDEX IF EXISTS public.ix_report_video_render_previews_task_type_ratio")
    op.execute("DROP TABLE IF EXISTS public.report_video_render_previews")
