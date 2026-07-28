"""add Bilibili charging dashboard snapshots

Revision ID: 20260727_bilibili_charging
Revises: 20260724_comment_hash_indexes
Create Date: 2026-07-27
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "20260727_bilibili_charging"
down_revision = "20260724_comment_hash_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '5min'")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.bilibili_charging_snapshots (
            id varchar(36) PRIMARY KEY,
            competitor_id varchar(36) NOT NULL
                REFERENCES public.bilibili_competitors(id) ON DELETE CASCADE,
            snapshot_date date NOT NULL,
            charger_count integer NOT NULL,
            fetched_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source varchar(40) NOT NULL DEFAULT 'bilibili_elec_monthly',
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_bilibili_charging_snapshot_competitor_date
                UNIQUE (competitor_id, snapshot_date),
            CONSTRAINT ck_bilibili_charging_snapshot_count_nonnegative
                CHECK (charger_count >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bilibili_charging_snapshots_competitor_id "
        "ON public.bilibili_charging_snapshots (competitor_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bilibili_charging_snapshots_snapshot_date "
        "ON public.bilibili_charging_snapshots (snapshot_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bilibili_charging_snapshots_date_competitor "
        "ON public.bilibili_charging_snapshots (snapshot_date, competitor_id)"
    )
    op.execute(
        """
        INSERT INTO public.bilibili_competitors (
            id, name, uid, content_type, source_url, active,
            created_at, updated_at
        )
        VALUES (
            'bili-report-strawberry',
            '草莓研报',
            '532653650',
            'report',
            'https://space.bilibili.com/532653650/video',
            true,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (uid) DO UPDATE SET
            name = EXCLUDED.name,
            source_url = EXCLUDED.source_url,
            active = true,
            updated_at = CURRENT_TIMESTAMP
        """
    )
    op.get_bind().execute(
        sa.text(
            """
        INSERT INTO public.system_configs (
            key, value, type, scope, status, description, created_at, updated_at
        )
        VALUES (
            'bilibili_charging_dashboard.pricing',
            :config_value,
            'bilibili_charging_dashboard',
            'global',
            1,
            'B站充电人数看板：按 UID 配置新增金额、到手比例和月收入单价',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (key, scope) DO NOTHING
        """
        ),
        {
            "config_value": json.dumps(
                {
                    "default": {
                        "revenue_per_new": 30,
                        "cash_rate": 0.4,
                        "monthly_per_charger": 10,
                    },
                    "overrides": {
                        "3546604889967195": {
                            "revenue_per_new": 50,
                            "cash_rate": 0.4,
                            "monthly_per_charger": 20,
                        }
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
    )
    op.get_bind().execute(
        sa.text(
            """
        INSERT INTO public.system_configs (
            key, value, type, scope, status, description, created_at, updated_at
        )
        VALUES (
            'bilibili_charging_dashboard.schedule',
            :config_value,
            'bilibili_charging_dashboard',
            'global',
            1,
            'B站充电人数看板自动采集计划',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (key, scope) DO NOTHING
        """
        ),
        {
            "config_value": json.dumps(
                {
                    "enabled": True,
                    "daily_at": "00:00",
                    "timezone": "Asia/Shanghai",
                    "notify_milestones": True,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.bilibili_charging_snapshots")
