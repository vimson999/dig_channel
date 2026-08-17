"""add durable sidebar browser submission receipts

Revision ID: 20260817_zsxq_browser_submission
Revises: 20260731_zsxq_insights_query_indexes
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260817_zsxq_browser_submission"
down_revision = "20260731_zsxq_insights_query_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "zsxq_browser_submissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_path", sa.String(length=600), nullable=False),
        sa.Column("celery_task_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kind", "run_id", name="uq_zsxq_browser_submissions_kind_run"
        ),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_zsxq_browser_submissions_idempotency_key"),
        "zsxq_browser_submissions",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_zsxq_browser_submissions_status"),
        "zsxq_browser_submissions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_browser_submissions_status_updated",
        "zsxq_browser_submissions",
        ["status", "updated_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_zsxq_browser_submissions_status_updated",
        table_name="zsxq_browser_submissions",
    )
    op.drop_index(
        op.f("ix_zsxq_browser_submissions_status"),
        table_name="zsxq_browser_submissions",
    )
    op.drop_index(
        op.f("ix_zsxq_browser_submissions_idempotency_key"),
        table_name="zsxq_browser_submissions",
    )
    op.drop_table("zsxq_browser_submissions")
