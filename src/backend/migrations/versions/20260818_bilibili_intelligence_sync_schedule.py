"""schedule hourly Bilibili intelligence sync with result notifications

Revision ID: 20260818_bilibili_intelligence_sync
Revises: 20260817_zsxq_browser_submission
Create Date: 2026-08-18
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "20260818_bilibili_intelligence_sync"
down_revision = "20260817_zsxq_browser_submission"
branch_labels = None
depends_on = None


CONFIG_KEY = "bilibili_intelligence_sync.schedule"
CONFIG_TYPE = "bilibili_intelligence_sync"
CONFIG_SCOPE = "global"


def upgrade():
    payload = json.dumps(
        {
            "enabled": True,
            "interval_minutes": 60,
            "timezone": "Asia/Shanghai",
            "page_size": 30,
            "notify_milestones": True,
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
                "B站内容情报台每小时同步启用监控源投稿，并发送成功、部分失败或失败汇总邮件"
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
