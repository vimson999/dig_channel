"""persist canonical cleaned content-pipeline outputs

Revision ID: 20260826_content_pipeline_canonical_cleaned_assets
Revises: 20260825_zsxq_history_api_clients
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_content_pipeline_canonical_cleaned_assets"
down_revision = "20260825_zsxq_history_api_clients"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "content_pipeline_assets",
        sa.Column("cleaned_path", sa.String(length=600), nullable=True),
    )
    op.add_column(
        "content_pipeline_assets",
        sa.Column("cleaned_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "content_pipeline_assets",
        sa.Column("cleaned_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "content_pipeline_assets",
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("content_pipeline_assets", "cleaned_at")
    op.drop_column("content_pipeline_assets", "cleaned_bytes")
    op.drop_column("content_pipeline_assets", "cleaned_sha256")
    op.drop_column("content_pipeline_assets", "cleaned_path")
