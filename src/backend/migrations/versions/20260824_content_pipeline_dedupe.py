"""add shared content-pipeline admission fingerprints

Revision ID: 20260824_content_pipeline_dedupe
Revises: 20260818_bilibili_intelligence_sync
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_content_pipeline_dedupe"
down_revision = "20260818_bilibili_intelligence_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "content_pipeline_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_task_id", sa.String(length=36), nullable=False),
        sa.Column("source_path", sa.String(length=600), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("source_mode", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["canonical_task_id"],
            ["publication_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_type",
            "content_sha256",
            name="uq_content_pipeline_assets_type_sha256",
        ),
    )
    op.create_index(
        "ix_content_pipeline_assets_canonical_task_id",
        "content_pipeline_assets",
        ["canonical_task_id"],
        unique=False,
    )
    op.create_index(
        "ix_content_pipeline_assets_status",
        "content_pipeline_assets",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_content_pipeline_assets_task_status",
        "content_pipeline_assets",
        ["canonical_task_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_content_pipeline_assets_created",
        "content_pipeline_assets",
        ["created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_content_pipeline_assets_created", table_name="content_pipeline_assets")
    op.drop_index("ix_content_pipeline_assets_task_status", table_name="content_pipeline_assets")
    op.drop_index("ix_content_pipeline_assets_status", table_name="content_pipeline_assets")
    op.drop_index("ix_content_pipeline_assets_canonical_task_id", table_name="content_pipeline_assets")
    op.drop_table("content_pipeline_assets")
