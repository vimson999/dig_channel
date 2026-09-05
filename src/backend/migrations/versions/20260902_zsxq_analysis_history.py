"""add structured user-scoped ZSXQ analysis history projections

Revision ID: 20260902_zsxq_analysis_history
Revises: 20260829_workflow_notification_outbox
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260902_zsxq_analysis_history"
down_revision = "20260829_workflow_notification_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "zsxq_analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source_schema", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_exported_at", sa.DateTime(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "source_artifact_path", sa.String(length=600), nullable=False
        ),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("done_count", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("processing_count", sa.Integer(), nullable=False),
        sa.Column("total_download_count", sa.BigInteger(), nullable=False),
        sa.Column("download_count_min", sa.Integer(), nullable=False),
        sa.Column("download_count_max", sa.Integer(), nullable=False),
        sa.Column("download_count_avg", sa.Float(), nullable=False),
        sa.Column("download_count_median", sa.Float(), nullable=False),
        sa.Column("total_file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_duration_seconds", sa.BigInteger(), nullable=False),
        sa.Column("duration_avg_seconds", sa.Float(), nullable=False),
        sa.Column("duration_median_seconds", sa.Float(), nullable=False),
        sa.Column("duration_nonzero_count", sa.Integer(), nullable=False),
        sa.Column("unique_topic_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_file_id_count", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_zsxq_analysis_runs_user_id",
        "zsxq_analysis_runs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_runs_status",
        "zsxq_analysis_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_runs_user_kind_created",
        "zsxq_analysis_runs",
        ["user_id", "kind", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_runs_user_status_created",
        "zsxq_analysis_runs",
        ["user_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "zsxq_analysis_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(length=160), nullable=False),
        sa.Column("topic_id", sa.String(length=160), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("group_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.BigInteger(), nullable=False),
        sa.Column("upload_time_text", sa.String(length=100), nullable=True),
        sa.Column("upload_time", sa.DateTime(), nullable=True),
        sa.Column("is_duplicate_file", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["zsxq_analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "item_index",
            name="uq_zsxq_analysis_items_run_index",
        ),
    )
    op.create_index(
        "ix_zsxq_analysis_items_run_id",
        "zsxq_analysis_items",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_items_run_download",
        "zsxq_analysis_items",
        ["run_id", "download_count"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_items_run_status",
        "zsxq_analysis_items",
        ["run_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_items_run_topic",
        "zsxq_analysis_items",
        ["run_id", "topic_id"],
        unique=False,
    )

    op.create_table(
        "zsxq_analysis_topics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("topic_id", sa.String(length=160), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("done_count", sa.Integer(), nullable=False),
        sa.Column("total_download_count", sa.BigInteger(), nullable=False),
        sa.Column("max_download_count", sa.Integer(), nullable=False),
        sa.Column("latest_upload_time", sa.DateTime(), nullable=True),
        sa.Column("representative_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["zsxq_analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "topic_id",
            name="uq_zsxq_analysis_topics_run_topic",
        ),
    )
    op.create_index(
        "ix_zsxq_analysis_topics_run_id",
        "zsxq_analysis_topics",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_topics_run_rank",
        "zsxq_analysis_topics",
        ["run_id", "rank"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_topics_run_download",
        "zsxq_analysis_topics",
        ["run_id", "total_download_count"],
        unique=False,
    )

    op.create_table(
        "zsxq_analysis_keywords",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("keyword", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("total_download_count", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["zsxq_analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "keyword",
            name="uq_zsxq_analysis_keywords_run_keyword",
        ),
    )
    op.create_index(
        "ix_zsxq_analysis_keywords_run_id",
        "zsxq_analysis_keywords",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_zsxq_analysis_keywords_run_rank",
        "zsxq_analysis_keywords",
        ["run_id", "rank"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_zsxq_analysis_keywords_run_rank",
        table_name="zsxq_analysis_keywords",
    )
    op.drop_index(
        "ix_zsxq_analysis_keywords_run_id", table_name="zsxq_analysis_keywords"
    )
    op.drop_table("zsxq_analysis_keywords")

    op.drop_index(
        "ix_zsxq_analysis_topics_run_download",
        table_name="zsxq_analysis_topics",
    )
    op.drop_index(
        "ix_zsxq_analysis_topics_run_rank", table_name="zsxq_analysis_topics"
    )
    op.drop_index(
        "ix_zsxq_analysis_topics_run_id", table_name="zsxq_analysis_topics"
    )
    op.drop_table("zsxq_analysis_topics")

    op.drop_index(
        "ix_zsxq_analysis_items_run_topic", table_name="zsxq_analysis_items"
    )
    op.drop_index(
        "ix_zsxq_analysis_items_run_status", table_name="zsxq_analysis_items"
    )
    op.drop_index(
        "ix_zsxq_analysis_items_run_download", table_name="zsxq_analysis_items"
    )
    op.drop_index(
        "ix_zsxq_analysis_items_run_id", table_name="zsxq_analysis_items"
    )
    op.drop_table("zsxq_analysis_items")

    op.drop_index(
        "ix_zsxq_analysis_runs_user_status_created",
        table_name="zsxq_analysis_runs",
    )
    op.drop_index(
        "ix_zsxq_analysis_runs_user_kind_created",
        table_name="zsxq_analysis_runs",
    )
    op.drop_index(
        "ix_zsxq_analysis_runs_status", table_name="zsxq_analysis_runs"
    )
    op.drop_index(
        "ix_zsxq_analysis_runs_user_id", table_name="zsxq_analysis_runs"
    )
    op.drop_table("zsxq_analysis_runs")
