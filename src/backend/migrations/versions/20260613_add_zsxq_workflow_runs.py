"""add zsxq workflow runs table

Revision ID: 20260613_add_zsxq_workflow_runs
Revises: None
Create Date: 2026-06-13

"""
from alembic import op
import sqlalchemy as sa


revision = "20260613_add_zsxq_workflow_runs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "zsxq_workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("group_name", sa.String(length=255), nullable=True),
        sa.Column("trigger", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("current_stage", sa.String(length=20), nullable=False, server_default="init"),
        sa.Column("round", sa.Integer(), nullable=True),
        sa.Column("max_rounds", sa.Integer(), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.Column("stage_results", sa.JSON(), nullable=True),
        sa.Column("sync_run_id", sa.String(length=36), nullable=True),
        sa.Column("clean_task_ids", sa.JSON(), nullable=True),
        sa.Column("publish_task_ids", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        schema="public",
    )
    op.create_index("ix_zsxq_workflow_runs_batch_id", "zsxq_workflow_runs", ["batch_id"], schema="public")
    op.create_index("ix_zsxq_workflow_runs_group_id", "zsxq_workflow_runs", ["group_id"], schema="public")
    op.create_index("ix_zsxq_workflow_runs_trigger", "zsxq_workflow_runs", ["trigger"], schema="public")
    op.create_index("ix_zsxq_workflow_runs_status", "zsxq_workflow_runs", ["status"], schema="public")
    op.create_index("ix_zsxq_workflow_runs_current_stage", "zsxq_workflow_runs", ["current_stage"], schema="public")
    op.create_index("ix_zsxq_workflow_runs_sync_run_id", "zsxq_workflow_runs", ["sync_run_id"], schema="public")
    op.create_index("ix_zsxq_workflow_runs_created_at", "zsxq_workflow_runs", ["created_at"], schema="public")


def downgrade():
    op.drop_index("ix_zsxq_workflow_runs_created_at", table_name="zsxq_workflow_runs", schema="public")
    op.drop_index("ix_zsxq_workflow_runs_sync_run_id", table_name="zsxq_workflow_runs", schema="public")
    op.drop_index("ix_zsxq_workflow_runs_current_stage", table_name="zsxq_workflow_runs", schema="public")
    op.drop_index("ix_zsxq_workflow_runs_status", table_name="zsxq_workflow_runs", schema="public")
    op.drop_index("ix_zsxq_workflow_runs_trigger", table_name="zsxq_workflow_runs", schema="public")
    op.drop_index("ix_zsxq_workflow_runs_group_id", table_name="zsxq_workflow_runs", schema="public")
    op.drop_index("ix_zsxq_workflow_runs_batch_id", table_name="zsxq_workflow_runs", schema="public")
    op.drop_table("zsxq_workflow_runs", schema="public")
