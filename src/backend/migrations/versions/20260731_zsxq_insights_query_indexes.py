"""speed up canonical topic selection for the insights dashboard

Revision ID: 20260731_zsxq_insights_query_indexes
Revises: 20260729_evidence_clean_visual
Create Date: 2026-07-31
"""

import logging

import sqlalchemy as sa
from alembic import op


revision = "20260731_zsxq_insights_query_indexes"
down_revision = "20260729_evidence_clean_visual"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_zsxq_topics_group_topic_preferred_synced_desc"
LOGGER = logging.getLogger("alembic.runtime.migration")


def _index_exists():
    return bool(
        op.get_bind()
        .execute(
            sa.text("SELECT to_regclass(:index_name)"),
            {"index_name": f"public.{INDEX_NAME}"},
        )
        .scalar()
    )


def _can_manage_topics_indexes():
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT pg_has_role(c.relowner, 'MEMBER')
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = 'zsxq_topics'
                LIMIT 1
                """
            )
        )
        .scalar()
    )


def upgrade():
    if _index_exists():
        return
    if not _can_manage_topics_indexes():
        LOGGER.warning(
            "Skipping optional insights index owned by another role: public.%s",
            INDEX_NAME,
        )
        return
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON zsxq_topics (
            group_id,
            topic_id,
            (
                CASE tab_label
                    WHEN '精华' THEN 0
                    WHEN '中标' THEN 1
                    WHEN '生财好事' THEN 2
                    ELSE 3
                END
            ),
            last_synced_at DESC
        )
        """
    )


def downgrade():
    if not _index_exists():
        return
    if not _can_manage_topics_indexes():
        LOGGER.warning(
            "Skipping optional insights index owned by another role: public.%s",
            INDEX_NAME,
        )
        return
    op.execute(f"DROP INDEX IF EXISTS public.{INDEX_NAME}")
