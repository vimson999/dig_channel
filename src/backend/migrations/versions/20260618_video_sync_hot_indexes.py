"""add video sync hot indexes

Revision ID: 20260618_video_sync_hot_indexes
Revises: 20260617_task_projection
Create Date: 2026-06-18

"""
from alembic import op


revision = "20260618_video_sync_hot_indexes"
down_revision = "20260617_task_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_videos_channel_active_published "
        "ON public.videos (channel_id, is_active, published_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_videos_channel_video_id "
        "ON public.videos (channel_id, video_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_video_data_date_video_id "
        "ON public.video_data (date, video_id)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_videos_channel_video_id")
    op.execute("DROP INDEX IF EXISTS public.ix_videos_channel_active_published")
    op.execute("DROP INDEX IF EXISTS public.ix_video_data_date_video_id")
