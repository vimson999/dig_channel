"""add zsxq sync run status hot indexes

Revision ID: 20260618_zsxq_sync_run_status_indexes
Revises: 20260618_publication_task_business_snapshot_index
Create Date: 2026-06-18

"""
from alembic import op
import sqlalchemy as sa


revision = "20260618_zsxq_sync_run_status_indexes"
down_revision = "20260618_publication_task_business_snapshot_index"
branch_labels = None
depends_on = None


def upgrade():
    _create_index_if_allowed(
        "ix_zsxq_sync_runs_group_status_updated",
        "zsxq_sync_runs",
        "CREATE INDEX ix_zsxq_sync_runs_group_status_updated "
        "ON public.zsxq_sync_runs (group_id, status, updated_at)",
    )
    _create_index_if_allowed(
        "ix_zsxq_sync_runs_status_updated",
        "zsxq_sync_runs",
        "CREATE INDEX ix_zsxq_sync_runs_status_updated "
        "ON public.zsxq_sync_runs (status, updated_at)",
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_sync_runs_status_updated")
    op.execute("DROP INDEX IF EXISTS public.ix_zsxq_sync_runs_group_status_updated")


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
