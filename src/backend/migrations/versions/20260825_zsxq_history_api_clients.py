"""add tenant-scoped app credentials for the ZSXQ history API

Revision ID: 20260825_zsxq_history_api_clients
Revises: 20260824_content_pipeline_dedupe
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_zsxq_history_api_clients"
down_revision = "20260824_content_pipeline_dedupe"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "zsxq_history_api_clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("app_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", name="uq_zsxq_history_api_clients_app_id"),
    )
    op.create_index(
        "ix_zsxq_history_api_clients_tenant_id",
        "zsxq_history_api_clients",
        ["tenant_id"],
    )
    op.create_index(
        "ix_zsxq_history_api_clients_status",
        "zsxq_history_api_clients",
        ["status"],
    )
    op.create_index(
        "ix_zsxq_history_api_clients_created_by_user_id",
        "zsxq_history_api_clients",
        ["created_by_user_id"],
    )


def downgrade():
    op.drop_index(
        "ix_zsxq_history_api_clients_created_by_user_id",
        table_name="zsxq_history_api_clients",
    )
    op.drop_index(
        "ix_zsxq_history_api_clients_status",
        table_name="zsxq_history_api_clients",
    )
    op.drop_index(
        "ix_zsxq_history_api_clients_tenant_id",
        table_name="zsxq_history_api_clients",
    )
    op.drop_table("zsxq_history_api_clients")
