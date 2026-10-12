"""add 胶囊播客 to Bilibili charging monitoring

Revision ID: 20260930_add_bili_charging_competitor
Revises: 20260912_web_submission_history
Create Date: 2026-09-30
"""

from alembic import op


revision = "20260930_add_bili_charging_competitor"
down_revision = "20260912_web_submission_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        INSERT INTO public.bilibili_competitors (
            id, name, uid, content_type, source_url, active,
            created_at, updated_at
        )
        VALUES (
            'bili-audio-capsule-podcast',
            '胶囊播客',
            '3493091757525962',
            'audio',
            'https://space.bilibili.com/3493091757525962/video',
            true,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (uid) DO UPDATE SET
            name = EXCLUDED.name,
            content_type = EXCLUDED.content_type,
            source_url = EXCLUDED.source_url,
            active = true,
            updated_at = CURRENT_TIMESTAMP
        """
    )


def downgrade():
    op.execute(
        """
        DELETE FROM public.bilibili_competitors
        WHERE id = 'bili-audio-capsule-podcast'
          AND uid = '3493091757525962'
        """
    )
