"""add database-backed ZSXQ topic favorites

Revision ID: 20260715_zsxq_topic_favorites
Revises: 20260713_mp3_hot_projection
"""

from alembic import op


revision = "20260715_zsxq_topic_favorites"
down_revision = "20260713_mp3_hot_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.zsxq_topic_favorites (
            id varchar(36) PRIMARY KEY,
            group_id varchar(32) NOT NULL,
            topic_id varchar(64) NOT NULL,
            created_at timestamp without time zone NOT NULL,
            updated_at timestamp without time zone NOT NULL,
            CONSTRAINT uq_zsxq_topic_favorites_group_topic UNIQUE (group_id, topic_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_favorites_group_id ON public.zsxq_topic_favorites (group_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_favorites_topic_id ON public.zsxq_topic_favorites (topic_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_favorites_group_created ON public.zsxq_topic_favorites (group_id, created_at DESC)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.zsxq_topic_favorites")
