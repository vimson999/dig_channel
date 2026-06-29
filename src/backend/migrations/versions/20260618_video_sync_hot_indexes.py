"""add video sync hot indexes

Revision ID: 20260618_video_sync_hot_indexes
Revises: 20260617_task_projection
Create Date: 2026-06-18

"""
from alembic import op
import sqlalchemy as sa


revision = "20260618_video_sync_hot_indexes"
down_revision = "20260617_task_projection"
branch_labels = None
depends_on = None


def upgrade():
    _create_index_if_allowed(
        "ix_videos_channel_active_published",
        "videos",
        "CREATE INDEX ix_videos_channel_active_published "
        "ON public.videos (channel_id, is_active, published_at DESC)",
    )
    _create_index_if_allowed(
        "ix_videos_channel_video_id",
        "videos",
        "CREATE INDEX ix_videos_channel_video_id "
        "ON public.videos (channel_id, video_id)",
    )
    _create_index_if_allowed(
        "ix_video_data_date_video_id",
        "video_data",
        "CREATE INDEX ix_video_data_date_video_id "
        "ON public.video_data (date, video_id)",
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_videos_channel_video_id")
    op.execute("DROP INDEX IF EXISTS public.ix_videos_channel_active_published")
    op.execute("DROP INDEX IF EXISTS public.ix_video_data_date_video_id")


def _create_index_if_allowed(index_name, table_name, sql):
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT to_regclass(:index_name)"),
        {"index_name": f"public.{index_name}"},
    ).scalar()
    if exists:
        return
    can_create = bind.execute(
        sa.text(
            """
            SELECT pg_has_role(c.relowner, 'MEMBER')
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = :table_name
            LIMIT 1
            """
        ),
        {"table_name": table_name},
    ).scalar()
    if not can_create:
        return
    op.execute(sql)
