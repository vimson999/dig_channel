"""schedule the homepage YouTube channel sync daily at 00:03

Revision ID: 20260728_youtube_channel_sync
Revises: 20260727_bili_charging_daily
Create Date: 2026-07-28
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "20260728_youtube_channel_sync"
down_revision = "20260727_bili_charging_daily"
branch_labels = None
depends_on = None


CONFIG_KEY = "youtube_channel_sync.schedule"
CONFIG_TYPE = "youtube_channel_sync"
CONFIG_SCOPE = "global"


def upgrade():
    payload = json.dumps(
        {
            "enabled": True,
            "daily_at": "00:03",
            "timezone": "Asia/Shanghai",
            "notify_milestones": True,
            "use_parallel": True,
            "max_workers": 5,
            "active_only": True,
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
                "首页频道管理同步计划：每天 00:03（Asia/Shanghai）同步全部启用频道，"
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
