"""add report video hot indexes

Revision ID: 20260619_report_video_hot_indexes
Revises: 20260618_zsxq_sync_run_status_indexes
Create Date: 2026-06-19

"""
from alembic import op


revision = "20260619_report_video_hot_indexes"
down_revision = "20260618_zsxq_sync_run_status_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_video_reports_created_at_desc "
        "ON public.report_video_reports (created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_video_reports_auto_asset_sha256 "
        "ON public.report_video_reports ((raw_meta #>> '{auto_asset,source_sha256}')) "
        "WHERE (raw_meta #>> '{auto_asset,source_sha256}') IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_video_reports_auto_scan_sha256 "
        "ON public.report_video_reports ((raw_meta #>> '{auto_scan,source_sha256}')) "
        "WHERE (raw_meta #>> '{auto_scan,source_sha256}') IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_video_tasks_auto_asset_sha256 "
        "ON public.report_video_tasks ((result #>> '{auto_asset,source_sha256}')) "
        "WHERE (result #>> '{auto_asset,source_sha256}') IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_video_tasks_auto_scan_sha256 "
        "ON public.report_video_tasks ((result #>> '{auto_scan,source_sha256}')) "
        "WHERE (result #>> '{auto_scan,source_sha256}') IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_video_outputs_task_status "
        "ON public.report_video_outputs (task_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_status_updated_created "
        "ON public.zsxq_workflow_runs (status, updated_at DESC, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_group_status_created "
        "ON public.zsxq_workflow_runs (group_id, status, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_stale_init "
        "ON public.zsxq_workflow_runs (status, current_stage, created_at ASC) "
        "WHERE status = 'queued' AND current_stage = 'init'"
    )


def downgrade():
    for name in [
        "ix_zsxq_workflow_runs_stale_init",
        "ix_zsxq_workflow_runs_group_status_created",
        "ix_zsxq_workflow_runs_status_updated_created",
        "ix_report_video_outputs_task_status",
        "ix_report_video_tasks_auto_scan_sha256",
        "ix_report_video_tasks_auto_asset_sha256",
        "ix_report_video_reports_auto_scan_sha256",
        "ix_report_video_reports_auto_asset_sha256",
        "ix_report_video_reports_created_at_desc",
    ]:
        op.execute(f"DROP INDEX IF EXISTS public.{name}")
