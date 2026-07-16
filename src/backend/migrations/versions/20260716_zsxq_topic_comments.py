"""add structured ZSXQ topic comments

Revision ID: 20260716_zsxq_topic_comments
Revises: 20260716_user_auth_version
"""

from alembic import op


revision = "20260716_zsxq_topic_comments"
down_revision = "20260716_user_auth_version"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.zsxq_topic_comments (
            id varchar(36) PRIMARY KEY,
            group_id varchar(32) NOT NULL,
            tab_id varchar(128),
            topic_id varchar(64) NOT NULL,
            comment_id varchar(64) NOT NULL,
            parent_comment_id varchar(64),
            owner_user_id varchar(64),
            owner_name varchar(255),
            text text,
            create_time varchar(64),
            likes_count integer NOT NULL DEFAULT 0,
            replies_count integer NOT NULL DEFAULT 0,
            rewards_count integer NOT NULL DEFAULT 0,
            sticky boolean NOT NULL DEFAULT false,
            raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            first_synced_at timestamp without time zone NOT NULL,
            last_synced_at timestamp without time zone NOT NULL,
            CONSTRAINT uq_zsxq_topic_comments_group_topic_comment UNIQUE (group_id, topic_id, comment_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_group_id ON public.zsxq_topic_comments (group_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_tab_id ON public.zsxq_topic_comments (tab_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_topic_id ON public.zsxq_topic_comments (topic_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_comment_id ON public.zsxq_topic_comments (comment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_parent_comment_id ON public.zsxq_topic_comments (parent_comment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_owner_user_id ON public.zsxq_topic_comments (owner_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_owner_name ON public.zsxq_topic_comments (owner_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_create_time ON public.zsxq_topic_comments (create_time)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_group_topic_created ON public.zsxq_topic_comments (group_id, topic_id, create_time)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_zsxq_topic_comments_owner_created ON public.zsxq_topic_comments (owner_name, create_time DESC)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.zsxq_topic_comments")
