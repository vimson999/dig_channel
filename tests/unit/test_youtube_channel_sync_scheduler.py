import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import Channel, ChannelData, SystemConfig, User, db
import run_youtube_channel_sync_scheduler as runner
from services import youtube_channel_sync_schedule_service as schedule_service
from services.youtube_sync_service import YouTubeSyncService


class YouTubeChannelSyncScheduleTest(unittest.TestCase):
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

    def test_schedule_is_loaded_from_system_configs(self):
        db.session.add(
            SystemConfig(
                key=schedule_service.CONFIG_KEY,
                scope=schedule_service.CONFIG_SCOPE,
                type=schedule_service.CONFIG_TYPE,
                status=1,
                value=json.dumps(
                    {
                        "enabled": True,
                        "daily_at": "00:03",
                        "timezone": "Asia/Shanghai",
                        "notify_milestones": True,
                        "use_parallel": True,
                        "max_workers": 5,
                        "active_only": True,
                    }
                ),
            )
        )
        db.session.commit()

        self.assertEqual(
            schedule_service.load_schedule_config(),
            {
                "enabled": True,
                "daily_at": "00:03",
                "timezone": "Asia/Shanghai",
                "notify_milestones": True,
                "use_parallel": True,
                "max_workers": 5,
                "active_only": True,
            },
        )

    def test_invalid_schedule_values_fall_back_and_bound_workers(self):
        db.session.add(
            SystemConfig(
                key=schedule_service.CONFIG_KEY,
                scope=schedule_service.CONFIG_SCOPE,
                type=schedule_service.CONFIG_TYPE,
                status=1,
                value=json.dumps(
                    {
                        "daily_at": "99:99",
                        "timezone": "Mars/Base",
                        "max_workers": 99,
                        "notify_milestones": "false",
                    }
                ),
            )
        )
        db.session.commit()

        config = schedule_service.load_schedule_config()

        self.assertEqual(config["daily_at"], "00:03")
        self.assertEqual(config["timezone"], "Asia/Shanghai")
        self.assertEqual(config["max_workers"], 10)
        self.assertFalse(config["notify_milestones"])

    def test_channel_sync_releases_read_session_before_youtube_request(self):
        user = User(
            username="sync-user",
            email="sync@example.com",
            password_hash="test",
        )
        db.session.add(user)
        db.session.flush()
        channel = Channel(
            user_id=user.id,
            name="频道",
            channel_id="UC-test",
            url="https://youtube.com/channel/UC-test",
            cpm=1,
            is_active=True,
        )
        db.session.add(channel)
        db.session.commit()
        channel_id = channel.id

        release = Mock(side_effect=lambda: db.session.remove())
        youtube_api = Mock()

        def get_stats(_channel_id):
            self.assertTrue(release.called)
            return {
                "subscriber_count": 100,
                "view_count": 1000,
                "video_count": 10,
            }

        youtube_api.get_channel_statistics.side_effect = get_stats
        sync_service = YouTubeSyncService.__new__(YouTubeSyncService)
        sync_service.youtube_api = youtube_api

        with patch(
            "services.youtube_sync_service.release_db_session_quietly",
            release,
        ):
            result = sync_service.sync_channel_data(
                channel_id,
                target_date=date(2026, 7, 28),
            )

        self.assertTrue(result["success"])
        self.assertEqual(ChannelData.query.count(), 1)

    def test_scheduler_emits_started_and_completed_milestones(self):
        sync_result = {
            "success": True,
            "message": "ok",
            "data": {
                "total_channels": 2,
                "success_count": 2,
                "failed_count": 0,
                "max_workers": 5,
                "duration_seconds": 1.2,
            },
        }
        events = []
        fake_service = Mock()
        fake_service.sync_all_channels_parallel.return_value = sync_result

        with (
            patch.object(
                runner,
                "load_schedule_config",
                return_value={
                    "enabled": True,
                    "daily_at": "00:03",
                    "timezone": "Asia/Shanghai",
                    "notify_milestones": True,
                    "use_parallel": True,
                    "max_workers": 5,
                    "active_only": True,
                },
            ),
            patch.object(
                runner,
                "OptimizedChannelSyncService",
                return_value=fake_service,
            ),
            patch.object(
                runner,
                "_enqueue_notification",
                side_effect=lambda event_type, **_kwargs: events.append(event_type),
            ),
            patch.object(runner, "release_db_session_quietly"),
        ):
            result = runner._run_once(self.app, "test")

        self.assertTrue(result["success"])
        self.assertEqual(events, ["workflow_started", "sync_completed"])

    def test_partial_sync_notification_keeps_failed_channel_details(self):
        sync_result = {
            "success": True,
            "message": "并行同步完成，共处理 2 个频道，成功 1 个，失败 1 个。",
            "data": {
                "total_channels": 2,
                "success_count": 1,
                "failed_count": 1,
                "results": [
                    {
                        "channel_id": 21,
                        "channel_name": "Smartpet Video AI",
                        "success": False,
                        "message": "无法获取频道统计数据",
                        "data": None,
                    },
                    {
                        "channel_id": 1,
                        "channel_name": "正常频道",
                        "success": True,
                        "message": "频道数据同步成功",
                        "data": {"subscribers": 1},
                    },
                ],
                "max_workers": 5,
                "duration_seconds": 1.2,
            },
        }
        notifications = []
        fake_service = Mock()
        fake_service.sync_all_channels_parallel.return_value = sync_result

        with (
            patch.object(
                runner,
                "load_schedule_config",
                return_value={
                    "enabled": True,
                    "daily_at": "00:03",
                    "timezone": "Asia/Shanghai",
                    "notify_milestones": True,
                    "use_parallel": True,
                    "max_workers": 5,
                    "active_only": True,
                },
            ),
            patch.object(
                runner,
                "OptimizedChannelSyncService",
                return_value=fake_service,
            ),
            patch.object(
                runner,
                "_enqueue_notification",
                side_effect=lambda event_type, **kwargs: notifications.append(
                    (event_type, kwargs)
                ),
            ),
            patch.object(runner, "release_db_session_quietly"),
        ):
            runner._run_once(self.app, "test")

        event_type, payload = notifications[-1]
        self.assertEqual(event_type, "sync_failed")
        self.assertEqual(
            payload["summary"]["failures"],
            [
                {
                    "channel_id": 21,
                    "channel_name": "Smartpet Video AI",
                    "message": "无法获取频道统计数据",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
