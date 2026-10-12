import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import Channel, SystemConfig, User, db
import run_youtube_video_sync_scheduler as runner
from services import youtube_video_sync_schedule_service as schedule_service
from services.optimized_video_sync_service import OptimizedVideoSyncService


class YouTubeVideoSyncScheduleTest(unittest.TestCase):
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
                        "daily_at": "01:00",
                        "timezone": "Asia/Shanghai",
                        "days_back": 3,
                        "notify_milestones": True,
                        "max_workers": 5,
                    }
                ),
            )
        )
        db.session.commit()

        self.assertEqual(
            schedule_service.load_schedule_config(),
            {
                "enabled": True,
                "daily_at": "01:00",
                "timezone": "Asia/Shanghai",
                "days_back": 3,
                "notify_milestones": True,
                "max_workers": 5,
            },
        )

    def test_invalid_values_fall_back_and_are_bounded(self):
        db.session.add(
            SystemConfig(
                key=schedule_service.CONFIG_KEY,
                scope=schedule_service.CONFIG_SCOPE,
                type=schedule_service.CONFIG_TYPE,
                status=1,
                value=json.dumps(
                    {
                        "daily_at": "31:00",
                        "timezone": "Mars/Base",
                        "days_back": 99,
                        "max_workers": 99,
                        "notify_milestones": "false",
                    }
                ),
            )
        )
        db.session.commit()

        config = schedule_service.load_schedule_config()

        self.assertEqual(config["daily_at"], "01:00")
        self.assertEqual(config["timezone"], "Asia/Shanghai")
        self.assertEqual(config["days_back"], 30)
        self.assertEqual(config["max_workers"], 10)
        self.assertFalse(config["notify_milestones"])

    def test_remote_video_fetch_happens_after_read_session_release(self):
        user = User(
            username="video-user",
            email="video@example.com",
            password_hash="test",
        )
        db.session.add(user)
        db.session.flush()
        channel = Channel(
            user_id=user.id,
            name="频道",
            channel_id="UC-video",
            url="https://youtube.com/channel/UC-video",
            cpm=1,
            is_active=True,
        )
        db.session.add(channel)
        db.session.commit()
        channel_id = channel.id

        release = Mock(side_effect=lambda: db.session.remove())
        youtube_api = Mock()

        def fetch(*_args, **_kwargs):
            self.assertTrue(release.called)
            return {"success": True, "data": {"videos": []}}

        youtube_api.get_channel_videos.side_effect = fetch
        service = OptimizedVideoSyncService.__new__(OptimizedVideoSyncService)
        service.youtube_api = youtube_api

        with patch(
            "services.optimized_video_sync_service.release_db_session_quietly",
            release,
        ):
            result = OptimizedVideoSyncService._get_channel_videos_cached.__wrapped__(
                service,
                "UC-video",
                3,
                channel_id,
            )

        self.assertTrue(result["success"])

    def test_scheduler_emits_started_and_completed_milestones(self):
        result_payload = {
            "success": True,
            "message": "ok",
            "data": {
                "processed": 2,
                "errors": 0,
                "new_videos": 4,
                "updated_videos": 6,
            },
        }
        fake_service = Mock()
        fake_service.sync_all_channels_videos_parallel.return_value = result_payload
        events = []

        with (
            patch.object(
                runner,
                "load_schedule_config",
                return_value={
                    "enabled": True,
                    "daily_at": "01:00",
                    "timezone": "Asia/Shanghai",
                    "days_back": 3,
                    "notify_milestones": True,
                    "max_workers": 5,
                },
            ),
            patch.object(
                runner,
                "OptimizedVideoSyncService",
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
        fake_service.sync_all_channels_videos_parallel.assert_called_once_with(
            days_back=3,
            user_id=None,
            max_workers=5,
        )

    def test_partial_sync_notification_keeps_failed_channel_details(self):
        result_payload = {
            "success": True,
            "message": "并行同步完成: 处理 1 个频道",
            "data": {
                "processed": 1,
                "errors": 1,
                "new_videos": 2,
                "updated_videos": 3,
                "details": [
                    {
                        "channel_id": 56,
                        "channel_name": "数字指南针",
                        "status": "error",
                        "message": "未找到远端频道",
                    },
                    {
                        "channel_id": 1,
                        "channel_name": "正常频道",
                        "status": "success",
                        "new_videos": 2,
                        "updated_videos": 3,
                        "from_cache": False,
                    },
                ],
            },
        }
        notifications = []
        fake_service = Mock()
        fake_service.sync_all_channels_videos_parallel.return_value = result_payload

        with (
            patch.object(
                runner,
                "load_schedule_config",
                return_value={
                    "enabled": True,
                    "daily_at": "01:00",
                    "timezone": "Asia/Shanghai",
                    "days_back": 3,
                    "notify_milestones": True,
                    "max_workers": 5,
                },
            ),
            patch.object(
                runner,
                "OptimizedVideoSyncService",
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
                    "channel_id": 56,
                    "channel_name": "数字指南针",
                    "message": "未找到远端频道",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
