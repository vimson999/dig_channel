"""add zsxq attachment status hot indexes

Revision ID: 20260618_zsxq_attachment_status_indexes
Revises: 20260618_video_sync_hot_indexes
Create Date: 2026-06-18

"""
from alembic import op


revision = "20260618_zsxq_attachment_status_indexes"
down_revision = "20260618_video_sync_hot_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_topic_status "
        "ON public.zsxq_attachments (group_id, topic_id, download_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_status_file "
        "ON public.zsxq_attachments (group_id, tab_id, download_status, file_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_attachments_group_tab_status_key "
        "ON public.zsxq_attachments (group_id, tab_id, download_status, attachment_key)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_attachments_group_tab_status_key")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_attachments_group_tab_status_file")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_attachments_group_topic_status")
