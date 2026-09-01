"""add durable workflow notification outbox and provider breaker

Revision ID: 20260829_workflow_notification_outbox
Revises: 20260826_content_pipeline_canonical_cleaned_assets
Create Date: 2026-08-29
"""

import sqlalchemy as sa
from alembic import op


revision = "20260829_workflow_notification_outbox"
down_revision = "20260826_content_pipeline_canonical_cleaned_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("workflow_id", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=50), nullable=True),
        sa.Column("last_error_type", sa.String(length=160), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("first_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_workflow_notification_deliveries_dedupe_key",
        ),
    )
    op.create_index(
        "ix_workflow_notification_deliveries_provider_due",
        "workflow_notification_deliveries",
        ["provider", "status", "available_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_notification_deliveries_lease",
        "workflow_notification_deliveries",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_notification_deliveries_workflow",
        "workflow_notification_deliveries",
        ["workflow_id", "event_type", "created_at"],
        unique=False,
    )
    op.create_table(
        "workflow_notification_provider_states",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("open_until", sa.DateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_type", sa.String(length=160), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )


def downgrade():
    op.drop_table("workflow_notification_provider_states")
    op.drop_index(
        "ix_workflow_notification_deliveries_workflow",
        table_name="workflow_notification_deliveries",
    )
    op.drop_index(
        "ix_workflow_notification_deliveries_lease",
        table_name="workflow_notification_deliveries",
    )
    op.drop_index(
        "ix_workflow_notification_deliveries_provider_due",
        table_name="workflow_notification_deliveries",
    )
    op.drop_table("workflow_notification_deliveries")
