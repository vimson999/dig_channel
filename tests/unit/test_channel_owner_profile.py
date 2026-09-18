import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1.rankings import bp  # noqa: E402
from models import Channel, ChannelData, User, Video, db  # noqa: E402


class ChannelOwnerProfileApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="channel-owner-profile-test",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        owner = User(username="owner", email="owner@example.com", password_hash="fixture")
        other_user = User(username="other", email="other@example.com", password_hash="fixture")
        db.session.add_all([owner, other_user])
        db.session.flush()
        self.owner_id = owner.id

        self.channel = Channel(
            user_id=owner.id,
            name="频道主 A",
            channel_id="creator-a",
            url="https://youtube.example/creator-a",
            cpm=4.5,
            category="教育",
        )
        other_channel = Channel(
            user_id=other_user.id,
            name="不应泄露的频道",
            channel_id="other-creator",
            url="https://youtube.example/other",
            cpm=1,
        )
        db.session.add_all([self.channel, other_channel])
        db.session.flush()
        self.other_channel_id = other_channel.id

        latest_day = date(2026, 9, 15)
        for offset in range(0, 32):
            current_day = latest_day - timedelta(days=offset)
            db.session.add(ChannelData(
                channel_id=self.channel.id,
                date=current_day,
                subscribers=12000 - offset * 10,
                total_views=240000 - offset * 1000,
                video_count=20 - (1 if offset > 15 else 0),
                subscriber_change=10,
                view_change=1000,
                estimated_revenue=4.5,
            ))
        same_metrics_channel = Channel(
            user_id=owner.id,
            name="同指标频道",
            channel_id="same-metrics-channel",
            url="https://youtube.example/same-metrics",
            cpm=4.5,
        )
        late_sync_channel = Channel(
            user_id=owner.id,
            name="采集更晚的频道",
            channel_id="late-sync-channel",
            url="https://youtube.example/late-sync",
            cpm=4.5,
        )
        db.session.add_all([same_metrics_channel, late_sync_channel])
        db.session.flush()
        self.same_metrics_channel_id = same_metrics_channel.id
        for offset in range(0, 32):
            current_day = latest_day - timedelta(days=offset)
            db.session.add(ChannelData(
                channel_id=same_metrics_channel.id,
                date=current_day,
                subscribers=12000 - offset * 10,
                total_views=240000 - offset * 1000,
                video_count=20 - (1 if offset > 15 else 0),
                subscriber_change=10,
                view_change=1000,
                estimated_revenue=4.5,
            ))
        db.session.add_all([
            ChannelData(
                channel_id=late_sync_channel.id,
                date=latest_day,
                subscribers=100,
                total_views=1000,
                video_count=1,
                subscriber_change=1,
                view_change=100,
                estimated_revenue=4.5,
            ),
            ChannelData(
                channel_id=late_sync_channel.id,
                date=latest_day + timedelta(days=1),
                subscribers=200,
                total_views=2000,
                video_count=2,
                subscriber_change=100,
                view_change=1000,
                estimated_revenue=100,
            ),
        ])
        db.session.add(ChannelData(
            channel_id=other_channel.id,
            date=latest_day,
            subscribers=999999,
            total_views=999999,
            video_count=99,
            subscriber_change=999999,
            view_change=999999,
            estimated_revenue=999999,
        ))
        db.session.add_all([
            Video(
                user_id=owner.id,
                channel_id=self.channel.id,
                video_id="creator-a-video-1",
                title="最新一期",
                published_at=datetime(2026, 9, 14, 8, 0),
                view_count=3200,
                like_count=120,
                comment_count=12,
            ),
            Video(
                user_id=owner.id,
                channel_id=self.channel.id,
                video_id="creator-a-video-2",
                title="表现最好的一期",
                published_at=datetime(2026, 9, 10, 8, 0),
                view_count=8900,
                like_count=230,
                comment_count=40,
            ),
        ])
        db.session.commit()
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _get_as_owner(self, path):
        with patch("utils.auth_utils.get_current_user", return_value=SimpleNamespace(id=self.owner_id)):
            return self.client.get(path)

    def test_profile_returns_one_owned_channel_with_its_metrics_trend_and_videos(self):
        response = self._get_as_owner(f"/api/v1/rankings/channel/{self.channel.id}/profile")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["channel"], {
            "id": self.channel.id,
            "name": "频道主 A",
            "youtube_id": "creator-a",
            "url": "https://youtube.example/creator-a",
            "category": "教育",
            "cpm": 4.5,
        })
        self.assertEqual(payload["latest_metrics"], {
            "date": "2026-09-15",
            "subscribers": 12000,
            "total_views": 240000,
            "video_count": 20,
        })
        self.assertEqual(payload["recent_performance"], {
            "days": 30,
            "revenue": 135.0,
            "subscriber_change": 300,
            "view_change": 30000,
        })
        self.assertEqual(len(payload["trend"]), 32)
        self.assertEqual(payload["trend"][0]["date"], "2026-08-15")
        self.assertEqual(payload["trend"][-1]["estimated_revenue"], 4.5)
        self.assertEqual([video["title"] for video in payload["recent_videos"]], ["最新一期", "表现最好的一期"])
        self.assertEqual(payload["top_video"]["title"], "表现最好的一期")
        self.assertEqual(payload["rankings"], {
            "as_of": "2026-09-15",
            "daily_revenue_rank": 1,
            "daily_subscriber_rank": 1,
            "monthly_revenue_rank": 1,
            "monthly_subscriber_rank": 1,
        })
        self.assertEqual(payload["periods"]["daily"][0], {
            "period": "2026-09-15",
            "subscribers": 12000,
            "total_views": 240000,
            "video_count": 20,
            "subscriber_change": 10,
            "view_change": 1000,
            "estimated_revenue": 4.5,
        })
        self.assertEqual(payload["periods"]["monthly"], [
            {
                "period": "2026-09",
                "subscribers": 12000,
                "total_views": 240000,
                "video_count": 20,
                "subscriber_change": 150,
                "view_change": 15000,
                "estimated_revenue": 67.5,
            },
            {
                "period": "2026-08",
                "subscribers": 11850,
                "total_views": 225000,
                "video_count": 20,
                "subscriber_change": 170,
                "view_change": 17000,
                "estimated_revenue": 76.5,
            },
        ])
        self.assertEqual(payload["periods"]["yearly"], [{
            "period": "2026",
            "subscribers": 12000,
            "total_views": 240000,
            "video_count": 20,
            "subscriber_change": 320,
            "view_change": 32000,
            "estimated_revenue": 144.0,
        }])

    def test_profile_ranks_tied_channels_in_the_same_order_as_the_rankings_page(self):
        response = self._get_as_owner(f"/api/v1/rankings/channel/{self.same_metrics_channel_id}/profile")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["rankings"], {
            "as_of": "2026-09-15",
            "daily_revenue_rank": 2,
            "daily_subscriber_rank": 2,
            "monthly_revenue_rank": 2,
            "monthly_subscriber_rank": 2,
        })

    def test_profile_hides_channels_owned_by_another_user(self):
        response = self._get_as_owner(f"/api/v1/rankings/channel/{self.other_channel_id}/profile")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
