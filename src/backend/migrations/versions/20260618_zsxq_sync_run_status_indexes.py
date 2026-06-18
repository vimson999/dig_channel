"""add zsxq sync run status hot indexes

Revision ID: 20260618_zsxq_sync_run_status_indexes
Revises: 20260618_publication_task_business_snapshot_index
Create Date: 2026-06-18

"""
from alembic import op


revision = "20260618_zsxq_sync_run_status_indexes"
down_revision = "20260618_publication_task_business_snapshot_index"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_group_status_updated "
        "ON public.zsxq_sync_runs (group_id, status, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_sync_runs_status_updated "
        "ON public.zsxq_sync_runs (status, updated_at)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_sync_runs_status_updated")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_sync_runs_group_status_updated")
