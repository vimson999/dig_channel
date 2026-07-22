"""全平台发布注册表和统一分发门面的契约测试。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import platform_publish_dispatch as dispatch  # noqa: E402


class PlatformPublishDispatchTest(unittest.TestCase):
    def test_registry_covers_all_supported_publish_platforms(self):
        routes = {route.platform: route for route in dispatch.platform_publish_routes()}

        self.assertEqual(
            set(routes),
            {"bilibili", "youtube", "douyin", "wechat_channels", "zsxq"},
        )
        self.assertEqual(routes["bilibili"].queue, "platform_upload")
        self.assertEqual(routes["douyin"].queue, "platform_upload")
        self.assertEqual(routes["wechat_channels"].queue, "platform_upload")
        self.assertEqual(routes["zsxq"].queue, "zsxq_publish")
        self.assertTrue(routes["zsxq"].guarded_submission)

    def test_platform_aliases_resolve_to_canonical_routes(self):
        self.assertEqual(dispatch.get_platform_publish_route("b站").platform, "bilibili")
        self.assertEqual(dispatch.get_platform_publish_route("channels").platform, "wechat_channels")
        self.assertEqual(dispatch.get_platform_publish_route("知识星球").platform, "zsxq")

    def test_unknown_platform_fails_before_enqueue(self):
        with self.assertRaisesRegex(ValueError, "不支持的发布平台"):
            dispatch.dispatch_platform_publish("missing", "task-1")

    def test_standard_dispatch_uses_registered_task_and_queue(self):
        celery_callable = Mock()
        celery_callable.apply_async.return_value = SimpleNamespace(id="celery-video-1")
        route = dispatch.PlatformPublishRoute(
            platform="fixture_video",
            label="测试视频平台",
            queue="fixture_upload",
            runtime_feature="fixture_video_publish",
            task_features=frozenset({"fixture_video_publish"}),
            task_loader=lambda: celery_callable,
            content_kind="video",
            transport="browser",
        )

        with patch.dict(dispatch._ROUTE_INDEX, {"fixture_video": route}):
            result = dispatch.dispatch_platform_publish("fixture_video", "task-1")

        celery_callable.apply_async.assert_called_once_with(
            args=["task-1"], queue="fixture_upload", retry=False
        )
        self.assertEqual(result["celery_id"], "celery-video-1")
        self.assertFalse(result["deferred"])

    def test_guarded_dispatch_reuses_runtime_admission(self):
        celery_callable = Mock()
        route = dispatch.PlatformPublishRoute(
            platform="fixture_guarded",
            label="测试准入平台",
            queue="fixture_guarded_queue",
            runtime_feature="fixture_guarded_publish",
            task_features=frozenset({"fixture_guarded_publish"}),
            task_loader=lambda: celery_callable,
            content_kind="topic_attachments",
            transport="api",
            guarded_submission=True,
        )
        guarded_result = {
            "async_result": SimpleNamespace(id="celery-guarded-1"),
            "admission": {"allowed": False, "reason": "feature_backlog"},
            "deferred": True,
            "deferred_state": {"status": "pending"},
            "countdown": 60,
        }

        with patch.dict(dispatch._ROUTE_INDEX, {"fixture_guarded": route}), patch(
            "services.runtime_guard.maybe_guarded_apply_async",
            return_value=guarded_result,
        ) as guarded:
            result = dispatch.dispatch_platform_publish(
                "fixture_guarded",
                "task-2",
                broker_task_id="broker-guarded-2",
            )

        guarded.assert_called_once_with(
            celery_callable,
            args=["task-2"],
            queue="fixture_guarded_queue",
            feature="fixture_guarded_publish",
            task_id="task-2",
            broker_task_id="broker-guarded-2",
            retry=False,
        )
        self.assertEqual(result["celery_id"], "celery-guarded-1")
        self.assertTrue(result["deferred"])

    def test_standard_dispatch_forwards_preallocated_broker_task_id(self):
        celery_callable = Mock()
        celery_callable.apply_async.return_value = SimpleNamespace(id="broker-video-1")
        route = dispatch.PlatformPublishRoute(
            platform="fixture_video_id",
            label="测试视频平台",
            queue="fixture_upload",
            runtime_feature="fixture_video_publish",
            task_features=frozenset({"fixture_video_publish"}),
            task_loader=lambda: celery_callable,
            content_kind="video",
            transport="browser",
        )

        with patch.dict(dispatch._ROUTE_INDEX, {"fixture_video_id": route}):
            result = dispatch.dispatch_platform_publish(
                "fixture_video_id",
                "task-1",
                broker_task_id="broker-video-1",
            )

        celery_callable.apply_async.assert_called_once_with(
            args=["task-1"],
            queue="fixture_upload",
            retry=False,
            task_id="broker-video-1",
        )
        self.assertEqual(result["celery_id"], "broker-video-1")

    def test_feature_reverse_lookup_refuses_ambiguous_publisher(self):
        self.assertIsNone(dispatch.route_for_unique_feature("publisher"))
        self.assertEqual(
            dispatch.route_for_unique_feature("douyin_upload_test").platform,
            "douyin",
        )
        self.assertEqual(
            dispatch.route_for_unique_feature("wechat_channels_upload_test").platform,
            "wechat_channels",
        )
        self.assertEqual(
            dispatch.route_for_unique_feature("zsxq_clean_output_publish").platform,
            "zsxq",
        )


if __name__ == "__main__":
    unittest.main()
