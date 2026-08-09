"""speed up canonical topic selection for the insights dashboard

Revision ID: 20260731_zsxq_insights_query_indexes
Revises: 20260729_evidence_clean_visual
Create Date: 2026-07-31
"""

from alembic import op


revision = "20260731_zsxq_insights_query_indexes"
down_revision = "20260729_evidence_clean_visual"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_zsxq_topics_group_topic_preferred_synced_desc"


def upgrade():
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
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
