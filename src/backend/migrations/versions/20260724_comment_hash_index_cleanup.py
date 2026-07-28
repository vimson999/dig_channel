"""avoid unchanged comment rewrites and remove redundant hot-table indexes

Revision ID: 20260724_comment_hash_indexes
Revises: 20260722_zsxq_topic_payload_hash
Create Date: 2026-07-24
"""
import logging

import sqlalchemy as sa
from alembic import op


revision = "20260724_comment_hash_indexes"
down_revision = "20260722_zsxq_topic_payload_hash"
branch_labels = None
depends_on = None


REDUNDANT_INDEXES = (
    "ix_external_documents_url_hash",
    "ix_publication_tasks_feature_status_updated",
    "ix_publication_tasks_feature_created_at_desc",
    "ix_publication_tasks_publisher_created_at_desc",
    "ix_publication_tasks_publisher_pending_updated_at",
    "ix_zsxq_topics_group_tab_type_create_desc",
    "ix_zsxq_topics_group_type_create_desc",
    "ix_zsxq_attachments_publish_status",
    "ix_zsxq_topic_comments_tab_id",
    "ix_zsxq_topic_comments_parent_comment_id",
    "ix_zsxq_topic_comments_owner_user_id",
    "ix_zsxq_topic_comments_owner_name",
    "ix_zsxq_topic_comments_create_time",
)
LOGGER = logging.getLogger("alembic.runtime.migration")


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '10min'")
    op.execute(
        "ALTER TABLE public.zsxq_topic_comments "
        "ADD COLUMN IF NOT EXISTS raw_payload_hash varchar(32)"
    )
    op.execute("""
        UPDATE public.zsxq_topic_comments
        SET raw_payload_hash = md5(raw_payload::jsonb::text)
        WHERE raw_payload IS NOT NULL
          AND raw_payload_hash IS NULL
    """)

    with op.get_context().autocommit_block():
        for index_name in REDUNDANT_INDEXES:
            can_drop = op.get_bind().execute(
                sa.text(
                    """
                    SELECT pg_has_role(t.relowner, 'MEMBER')
                    FROM pg_class AS i
                    JOIN pg_index AS x ON x.indexrelid = i.oid
                    JOIN pg_class AS t ON t.oid = x.indrelid
                    JOIN pg_namespace AS n ON n.oid = i.relnamespace
                    WHERE n.nspname = 'public'
                      AND i.relname = :index_name
                    LIMIT 1
                    """
                ),
                {"index_name": index_name},
            ).scalar()
            if can_drop is False:
                LOGGER.warning(
                    "Skipping redundant index owned by another role: public.%s",
                    index_name,
                )
                continue
            op.execute(
                f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}"
            )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_external_documents_url_hash "
            "ON public.external_documents (url_hash)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_publication_tasks_feature_status_updated "
            "ON public.publication_tasks (feature, status, updated_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_publication_tasks_feature_created_at_desc "
            "ON public.publication_tasks (((params ->> 'feature')::varchar), created_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_publication_tasks_publisher_created_at_desc "
            "ON public.publication_tasks (created_at DESC) "
            "WHERE (params ->> 'feature') IS NULL OR (params ->> 'feature') = 'publisher'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_publication_tasks_publisher_pending_updated_at "
            "ON public.publication_tasks (status, updated_at) "
            "WHERE (params ->> 'feature') IS NULL OR (params ->> 'feature') = 'publisher'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topics_group_tab_type_create_desc "
            "ON public.zsxq_topics (group_id, tab_id, topic_type, create_time DESC, last_synced_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topics_group_type_create_desc "
            "ON public.zsxq_topics (group_id, topic_type, create_time DESC, last_synced_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_attachments_publish_status "
            "ON public.zsxq_attachments (publish_status)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topic_comments_tab_id ON public.zsxq_topic_comments (tab_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topic_comments_parent_comment_id "
            "ON public.zsxq_topic_comments (parent_comment_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topic_comments_owner_user_id "
            "ON public.zsxq_topic_comments (owner_user_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topic_comments_owner_name "
            "ON public.zsxq_topic_comments (owner_name)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_zsxq_topic_comments_create_time "
            "ON public.zsxq_topic_comments (create_time)"
        )

    op.execute(
        "ALTER TABLE public.zsxq_topic_comments "
        "DROP COLUMN IF EXISTS raw_payload_hash"
    )
