import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import BilibiliChargingSnapshot, BilibiliCompetitor, SystemConfig, db
from services import bilibili_charging_dashboard_service as service


class BilibiliChargingDashboardTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _add_account(self, competitor_id, uid, name):
        row = BilibiliCompetitor(
            id=competitor_id,
            uid=uid,
            name=name,
            content_type="report",
            source_url=f"https://space.bilibili.com/{uid}/video",
            active=True,
        )
        db.session.add(row)
        return row

    def _add_snapshot(self, account, snapshot_date, charger_count):
        db.session.add(
            BilibiliChargingSnapshot(
                competitor_id=account.id,
                snapshot_date=snapshot_date,
                charger_count=charger_count,
                fetched_at=datetime.combine(snapshot_date, datetime.min.time()),
            )
        )

    def test_dashboard_uses_per_uid_pricing_and_previous_day_delta(self):
        standard = self._add_account("standard", "100", "普通账号")
        us_stock = self._add_account("us-stock", "3546604889967195", "美股研报局")
        db.session.add_all([
            BilibiliChargingSnapshot(
                competitor_id=standard.id,
                snapshot_date=date(2026, 7, 26),
                charger_count=100,
                fetched_at=datetime(2026, 7, 26, 1),
            ),
            BilibiliChargingSnapshot(
                competitor_id=standard.id,
                snapshot_date=date(2026, 7, 27),
                charger_count=105,
                fetched_at=datetime(2026, 7, 27, 1),
            ),
            BilibiliChargingSnapshot(
                competitor_id=us_stock.id,
                snapshot_date=date(2026, 7, 26),
                charger_count=100,
                fetched_at=datetime(2026, 7, 26, 1),
            ),
            BilibiliChargingSnapshot(
                competitor_id=us_stock.id,
                snapshot_date=date(2026, 7, 27),
                charger_count=103,
                fetched_at=datetime(2026, 7, 27, 1),
            ),
        ])
        db.session.commit()

        with patch.object(service, "business_date", return_value=date(2026, 7, 27)):
            payload = service.charging_dashboard_payload(days=2)

        by_uid = {item["uid"]: item for item in payload["accounts"]}
        standard_latest = by_uid["100"]["latest"]
        self.assertEqual(standard_latest["daily_growth"], 5)
        self.assertEqual(standard_latest["daily_revenue"], 150)
        self.assertEqual(standard_latest["daily_cash"], 60)
        self.assertEqual(standard_latest["monthly_income"], 1050)

        us_stock_latest = by_uid["3546604889967195"]["latest"]
        self.assertEqual(us_stock_latest["daily_growth"], 3)
        self.assertEqual(us_stock_latest["daily_revenue"], 150)
        self.assertEqual(us_stock_latest["daily_cash"], 60)
        self.assertEqual(us_stock_latest["monthly_income"], 2060)

    def test_first_snapshot_and_nonconsecutive_snapshot_do_not_invent_daily_growth(self):
        account = self._add_account("baseline", "999", "新账号")
        self._add_snapshot(account, date(2026, 9, 1), 100)
        self._add_snapshot(account, date(2026, 9, 3), 120)
        db.session.commit()
        with patch.object(service, "business_date", return_value=date(2026, 9, 3)):
            points = service.charging_dashboard_payload(days=3)["accounts"][0]["points"]
        for point in points:
            self.assertIsNone(point["daily_growth"])
            self.assertIsNone(point["daily_revenue"])
            self.assertIsNone(point["daily_cash"])
        self.assertEqual(points[-1]["monthly_income"], 1200)

    def test_dashboard_returns_rankings_for_rolling_day_week_and_month(self):
        alpha = self._add_account("alpha", "100", "甲账号")
        beta = self._add_account("beta", "200", "乙账号")
        insufficient = self._add_account("new", "300", "新账号")

        for snapshot_date, count in [
            (date(2026, 8, 8), 90),
            (date(2026, 9, 1), 100),
            (date(2026, 9, 7), 108),
            (date(2026, 9, 8), 110),
        ]:
            self._add_snapshot(alpha, snapshot_date, count)
        for snapshot_date, count in [
            (date(2026, 8, 9), 50),
            (date(2026, 9, 1), 70),
            (date(2026, 9, 7), 75),
            (date(2026, 9, 8), 77),
        ]:
            self._add_snapshot(beta, snapshot_date, count)
        self._add_snapshot(insufficient, date(2026, 9, 8), 200)
        db.session.commit()

        with patch.object(service, "business_date", return_value=date(2026, 9, 8)):
            payload = service.charging_dashboard_payload(days=31)

        rankings = payload["growth_rankings"]
        self.assertEqual(rankings["day"]["window_days"], 1)
        self.assertEqual(
            [(item["uid"], item["rank"], item["growth"]) for item in rankings["day"]["items"]],
            [("100", 1, 2), ("200", 2, 2), ("300", None, None)],
        )
        self.assertEqual(
            [(item["uid"], item["growth"]) for item in rankings["week"]["items"]],
            [("100", 10), ("200", 7), ("300", None)],
        )
        self.assertEqual(
            [(item["uid"], item["growth"]) for item in rankings["month"]["items"]],
            [("200", 27), ("100", 20), ("300", None)],
        )
        self.assertEqual(rankings["month"]["items"][0]["baseline_date"], "2026-08-09")
        self.assertEqual(rankings["month"]["items"][1]["baseline_date"], "2026-08-08")

    def test_month_ranking_is_independent_of_chart_range_when_sync_is_stale(self):
        account = self._add_account("stale", "400", "延迟账号")
        self._add_snapshot(account, date(2026, 5, 29), 100)
        self._add_snapshot(account, date(2026, 6, 30), 130)
        db.session.commit()

        with patch.object(service, "business_date", return_value=date(2026, 9, 8)):
            month_items = [
                service.charging_dashboard_payload(days=days)["growth_rankings"]["month"]["items"][0]
                for days in (31, 90, 366)
            ]

        self.assertEqual([item["growth"] for item in month_items], [30, 30, 30])
        self.assertEqual([item["latest_date"] for item in month_items], ["2026-06-30"] * 3)
        self.assertEqual([item["baseline_date"] for item in month_items], ["2026-05-29"] * 3)

    def test_same_day_collection_is_an_idempotent_upsert(self):
        account = self._add_account("one", "100", "账号")
        db.session.commit()
        responses = [
            [{"competitor_id": account.id, "uid": account.uid, "name": account.name, "charger_count": 10}],
            [{"competitor_id": account.id, "uid": account.uid, "name": account.name, "charger_count": 12}],
        ]

        async def fake_fetch_many(_accounts, _credential):
            return responses.pop(0)

        auth = {
            "credential_id": "credential",
            "sessdata": "sess",
            "bili_jct": "jct",
            "buvid3": "buvid",
            "dedeuserid": "1",
        }
        with (
            patch.object(service, "resolve_bilibili_auth", return_value=auth),
            patch.object(service, "_fetch_many", side_effect=fake_fetch_many),
            patch.object(service, "mark_credential_success"),
        ):
            first = service.collect_charging_snapshots(target_date=date(2026, 7, 27))
            second = service.collect_charging_snapshots(target_date=date(2026, 7, 27))

        self.assertEqual(first["synced"], 1)
        self.assertEqual(second["synced"], 1)
        snapshots = BilibiliChargingSnapshot.query.all()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].charger_count, 12)

    def test_schedule_and_pricing_are_loaded_from_system_configs(self):
        db.session.add_all([
            SystemConfig(
                key=service.CONFIG_KEY,
                scope=service.CONFIG_SCOPE,
                type=service.CONFIG_TYPE,
                status=1,
                value=json.dumps({
                    "default": {
                        "revenue_per_new": 25,
                        "cash_rate": 0.5,
                        "monthly_per_charger": 8,
                    },
                    "overrides": {},
                }),
            ),
            SystemConfig(
                key=service.SCHEDULE_CONFIG_KEY,
                scope=service.CONFIG_SCOPE,
                type=service.CONFIG_TYPE,
                status=1,
                value=json.dumps({
                    "enabled": True,
                    "daily_at": "00:00",
                    "timezone": "Asia/Shanghai",
                    "notify_milestones": True,
                }),
            ),
        ])
        db.session.commit()

        pricing = service.load_pricing_config()
        schedule = service.load_schedule_config()

        self.assertEqual(pricing["default"]["revenue_per_new"], 25.0)
        self.assertEqual(pricing["default"]["cash_rate"], 0.5)
        self.assertEqual(
            schedule,
            {
                "enabled": True,
                "daily_at": "00:00",
                "timezone": "Asia/Shanghai",
                "notify_milestones": True,
            },
        )

    def test_invalid_daily_schedule_values_fall_back_safely(self):
        db.session.add(
            SystemConfig(
                key=service.SCHEDULE_CONFIG_KEY,
                scope=service.CONFIG_SCOPE,
                type=service.CONFIG_TYPE,
                status=1,
                value=json.dumps({
                    "enabled": True,
                    "daily_at": "25:99",
                    "timezone": "Mars/Base",
                    "notify_milestones": "false",
                }),
            )
        )
        db.session.commit()

        self.assertEqual(
            service.load_schedule_config(),
            {
                "enabled": True,
                "daily_at": "00:00",
                "timezone": "Asia/Shanghai",
                "notify_milestones": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
