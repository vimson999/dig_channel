"""add publication task business snapshot index

Revision ID: 20260618_publication_task_business_snapshot_index
Revises: 20260618_zsxq_attachment_status_indexes
Create Date: 2026-06-18

"""
from alembic import op


revision = "20260618_publication_task_business_snapshot_index"
down_revision = "20260618_zsxq_attachment_status_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_publication_tasks_feature_status_updated_created_desc "
        "ON public.publication_tasks (feature, status, updated_at DESC, created_at DESC)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_publication_tasks_feature_status_updated_created_desc")
