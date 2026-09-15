import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1.rankings import bp  # noqa: E402
from models import Channel, ChannelData, User, db  # noqa: E402


class RankingPaginationTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="ranking-pagination-test",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        user = User(
            username="ranking-owner",
            email="ranking-owner@example.com",
            password_hash="fixture",
        )
        other_user = User(
            username="ranking-other",
            email="ranking-other@example.com",
            password_hash="fixture",
        )
        db.session.add_all([user, other_user])
        db.session.flush()
        self.user_id = user.id

        target_date = date(2026, 9, 15)
        for index in range(25):
            channel = Channel(
                user_id=user.id,
                name=f"频道 {index + 1:02d}",
                channel_id=f"owner-channel-{index + 1:02d}",
                url=f"https://example.com/owner/{index + 1}",
                cpm=3.25,
                category="AI工具",
                created_at=datetime(2026, 9, 1, 0, index),
            )
            db.session.add(channel)
            db.session.flush()
            db.session.add(
                ChannelData(
                    channel_id=channel.id,
                    date=target_date,
                    subscribers=index * 100,
                    total_views=index * 1000,
                    video_count=index,
                    subscriber_change=index + 1,
                    estimated_revenue=index + 1,
                )
            )
            db.session.add(
                ChannelData(
                    channel_id=channel.id,
                    date=date(2026, 8, 7),
                    subscribers=index * 100,
                    total_views=index * 1000,
                    video_count=index,
                    subscriber_change=index + 1,
                    estimated_revenue=index + 1,
                )
            )

        other_channel = Channel(
            user_id=other_user.id,
            name="其他用户频道",
            channel_id="other-channel",
            url="https://example.com/other",
            cpm=1,
        )
        db.session.add(other_channel)
        db.session.flush()
        db.session.add(
            ChannelData(
                channel_id=other_channel.id,
                date=target_date,
                subscribers=999999,
                total_views=999999,
                video_count=99,
                subscriber_change=999999,
                estimated_revenue=999999,
            )
        )
        db.session.commit()
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _get(self, path):
        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=self.user_id),
        ):
            return self.client.get(path)

    def test_each_ranking_endpoint_returns_the_requested_page_with_global_ranks(self):
        paths = [
            "/api/v1/rankings/daily-revenue?date=2026-09-15&page=2&per_page=10",
            "/api/v1/rankings/monthly-revenue?year=2026&month=9&page=2&per_page=10",
            "/api/v1/rankings/daily-subscriber?date=2026-09-15&page=2&per_page=10",
            "/api/v1/rankings/monthly-subscriber?year=2026&month=9&page=2&per_page=10",
            "/api/v1/rankings/yearly-revenue?year=2026&page=2&per_page=10",
            "/api/v1/rankings/yearly-subscriber?year=2026&page=2&per_page=10",
        ]

        for path in paths:
            with self.subTest(path=path):
                response = self._get(path)
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()["data"]
                self.assertEqual(payload["total_count"], 25)
                self.assertEqual(payload["pagination"], {
                    "page": 2,
                    "per_page": 10,
                    "total": 25,
                    "total_pages": 3,
                })
                self.assertEqual(len(payload["rankings"]), 10)
                self.assertEqual(
                    [item["rank"] for item in payload["rankings"]],
                    list(range(11, 21)),
                )
                self.assertNotIn(
                    "其他用户频道",
                    [item["channel_name"] for item in payload["rankings"]],
                )

    def test_yearly_rankings_aggregate_the_requested_calendar_year(self):
        revenue = self._get("/api/v1/rankings/yearly-revenue?year=2026&page=1&per_page=1")
        subscribers = self._get("/api/v1/rankings/yearly-subscriber?year=2026&page=1&per_page=1")

        self.assertEqual(revenue.status_code, 200)
        self.assertEqual(subscribers.status_code, 200)
        revenue_data = revenue.get_json()["data"]
        subscriber_data = subscribers.get_json()["data"]
        self.assertEqual(revenue_data["period"], {
            "start_date": "2026-01-01",
            "end_date": "2026-09-15",
        })
        self.assertEqual(revenue_data["rankings"][0]["total_revenue"], 50.0)
        self.assertEqual(subscriber_data["rankings"][0]["total_subscriber_change"], 50)

    def test_ranking_rows_include_the_channels_saved_reference_cpm_and_category(self):
        paths = [
            "/api/v1/rankings/daily-revenue?date=2026-09-15",
            "/api/v1/rankings/monthly-revenue?year=2026&month=9",
            "/api/v1/rankings/yearly-revenue?year=2026",
            "/api/v1/rankings/daily-subscriber?date=2026-09-15",
            "/api/v1/rankings/monthly-subscriber?year=2026&month=9",
            "/api/v1/rankings/yearly-subscriber?year=2026",
        ]

        for path in paths:
            with self.subTest(path=path):
                response = self._get(f"{path}&page=1&per_page=1")
                self.assertEqual(response.status_code, 200)
                first = response.get_json()["data"]["rankings"][0]
                self.assertEqual(first["cpm"], 3.25)
                self.assertEqual(first["category"], "AI工具")

    def test_invalid_pagination_is_rejected_instead_of_querying_an_implicit_page(self):
        for query in ("page=0&per_page=20", "page=1&per_page=0", "page=1&per_page=101"):
            with self.subTest(query=query):
                response = self._get(f"/api/v1/rankings/daily-revenue?{query}")
                self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
