"""schedule Bilibili charging snapshots once daily with notifications

Revision ID: 20260727_bili_charging_daily
Revises: 20260727_bilibili_charging
Create Date: 2026-07-27
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "20260727_bili_charging_daily"
down_revision = "20260727_bilibili_charging"
branch_labels = None
depends_on = None


def _write_schedule(payload):
    op.get_bind().execute(
        sa.text(
            """
            UPDATE public.system_configs
            SET value = :config_value,
                status = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = 'bilibili_charging_dashboard.schedule'
              AND scope = 'global'
            """
        ),
        {
            "config_value": json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
    )


def upgrade():
    _write_schedule(
        {
            "enabled": True,
            "daily_at": "00:00",
            "timezone": "Asia/Shanghai",
            "notify_milestones": True,
        }
    )


def downgrade():
    _write_schedule({"enabled": True, "interval_hours": 3})
