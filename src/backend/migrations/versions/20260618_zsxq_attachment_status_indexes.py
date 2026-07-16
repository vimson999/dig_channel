"""add zsxq attachment status hot indexes

Revision ID: 20260618_zsxq_attachment_status_indexes
Revises: 20260618_video_sync_hot_indexes
Create Date: 2026-06-18

"""
from alembic import op
import sqlalchemy as sa


revision = "20260618_zsxq_attachment_status_indexes"
down_revision = "20260618_video_sync_hot_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE public.alembic_version "
        "ALTER COLUMN version_num TYPE varchar(128)"
    )
    _create_index_if_allowed(
        "ix_zsxq_attachments_group_topic_status",
        "zsxq_attachments",
        "CREATE INDEX ix_zsxq_attachments_group_topic_status "
        "ON public.zsxq_attachments (group_id, topic_id, download_status)",
    )
    _create_index_if_allowed(
        "ix_zsxq_attachments_group_tab_status_file",
        "zsxq_attachments",
        "CREATE INDEX ix_zsxq_attachments_group_tab_status_file "
        "ON public.zsxq_attachments (group_id, tab_id, download_status, file_id)",
    )
    _create_index_if_allowed(
        "ix_zsxq_attachments_group_tab_status_key",
        "zsxq_attachments",
        "CREATE INDEX ix_zsxq_attachments_group_tab_status_key "
        "ON public.zsxq_attachments (group_id, tab_id, download_status, attachment_key)",
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_attachments_group_tab_status_key")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_attachments_group_tab_status_file")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_attachments_group_topic_status")


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
