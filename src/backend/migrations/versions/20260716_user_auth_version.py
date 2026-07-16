"""add revocable user authentication version

Revision ID: 20260716_user_auth_version
Revises: 20260715_zsxq_topic_favorites
Create Date: 2026-07-16
"""

from alembic import op


revision = "20260716_user_auth_version"
down_revision = "20260715_zsxq_topic_favorites"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_version integer NOT NULL DEFAULT 1"
    )


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS auth_version")
