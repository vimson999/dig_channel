"""schedule the video-management sync daily at 01:00 for three days

Revision ID: 20260728_youtube_video_sync
Revises: 20260728_youtube_channel_sync
Create Date: 2026-07-28
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "20260728_youtube_video_sync"
down_revision = "20260728_youtube_channel_sync"
branch_labels = None
depends_on = None


CONFIG_KEY = "youtube_video_sync.schedule"
CONFIG_TYPE = "youtube_video_sync"
CONFIG_SCOPE = "global"


def upgrade():
    payload = json.dumps(
        {
            "enabled": True,
            "daily_at": "01:00",
            "timezone": "Asia/Shanghai",
            "days_back": 3,
            "notify_milestones": True,
            "max_workers": 5,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO public.system_configs
                (key, value, type, scope, status, description, created_at, updated_at)
            VALUES
                (:key, :value, :type, :scope, 1, :description,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (key, scope)
            DO UPDATE SET
                value = EXCLUDED.value,
                type = EXCLUDED.type,
                status = EXCLUDED.status,
                description = EXCLUDED.description,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "key": CONFIG_KEY,
            "value": payload,
            "type": CONFIG_TYPE,
            "scope": CONFIG_SCOPE,
            "description": (
                "视频管理同步计划：每天 01:00（Asia/Shanghai）同步最近 3 天视频，"
                "并发送启动/完成/失败节点邮件"
            ),
        },
    )


def downgrade():
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM public.system_configs
            WHERE key = :key AND scope = :scope
            """
        ),
        {"key": CONFIG_KEY, "scope": CONFIG_SCOPE},
    )
