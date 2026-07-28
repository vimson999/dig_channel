"""add ZSXQ topic payload change fingerprint

Revision ID: 20260722_zsxq_topic_payload_hash
Revises: 20260722_report_audio_keyframe
Create Date: 2026-07-22
"""
from alembic import op


revision = "20260722_zsxq_topic_payload_hash"
down_revision = "20260722_report_audio_keyframe"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '5min'")
    op.execute(
        "ALTER TABLE public.zsxq_topics "
        "ADD COLUMN IF NOT EXISTS raw_payload_hash varchar(32)"
    )
    op.execute("""
        UPDATE public.zsxq_topics
        SET raw_payload_hash = md5(raw_payload::jsonb::text)
        WHERE raw_payload IS NOT NULL
          AND raw_payload_hash IS NULL
    """)


def downgrade():
    op.execute(
        "ALTER TABLE public.zsxq_topics "
        "DROP COLUMN IF EXISTS raw_payload_hash"
    )
