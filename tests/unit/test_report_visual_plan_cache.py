import sys
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_visual_plan as visual_plan_api  # noqa: E402
from utils import redis_client as shared_redis_client  # noqa: E402


class ReportVisualPlanCacheTest(unittest.TestCase):
    def setUp(self):
        visual_plan_api._clear_terminal_storyboard_cache()
        self.app = Flask(__name__)

    def tearDown(self):
        visual_plan_api._clear_terminal_storyboard_cache()

    def test_completed_job_detail_cache_skips_database_and_is_user_scoped(self):
        first_user = SimpleNamespace(id="user-1")
        second_user = SimpleNamespace(id="user-2")
        payload = {
            "success": True,
            "job_id": "job-1",
            "status": "succeeded",
            "data": {"segments": [{"sequence": 0, "script_text": "完整文案"}]},
        }
        with patch.object(visual_plan_api, "_storyboard_redis_client", return_value=None):
            visual_plan_api._cache_terminal_storyboard_job(first_user, "job-1", payload)

            with self.app.app_context():
                with patch.object(
                    visual_plan_api,
                    "_get_owned_visual_task",
                    side_effect=AssertionError("cached request must not query publication_tasks"),
                ):
                    response = visual_plan_api.get_job.__wrapped__(first_user, "job-1")

            self.assertEqual(payload, response.get_json())
            self.assertIsNone(visual_plan_api._cached_terminal_storyboard_job(second_user, "job-1"))

            response.get_json()["data"]["segments"][0]["script_text"] = "mutated"
            cached_again = visual_plan_api._cached_terminal_storyboard_job(first_user, "job-1")
            self.assertEqual("完整文案", cached_again["data"]["segments"][0]["script_text"])

    def test_completed_source_cache_survives_process_memory_loss(self):
        class FakeRedis:
            def __init__(self):
                self.values = {}

            def get(self, key):
                return self.values.get(key)

            def setex(self, key, _ttl, value):
                self.values[key] = value

            def delete(self, key):
                self.values.pop(key, None)

        user = SimpleNamespace(id="user-1")
        payload = {"success": True, "job_id": "job-1", "data": {"segments": [1, 2, 3]}}
        fake_redis = FakeRedis()

        with patch.object(visual_plan_api, "_storyboard_redis_client", return_value=fake_redis):
            visual_plan_api._cache_terminal_storyboard(user, "source-1", payload)
            visual_plan_api._TERMINAL_STORYBOARD_CACHE.clear()
            restored = visual_plan_api._cached_terminal_storyboard(user, "source-1")

        self.assertEqual(payload, restored)
        self.assertTrue(fake_redis.values)

    def test_shared_redis_client_honors_redis_url(self):
        import importlib

        sentinel = object()
        try:
            with patch.dict(os.environ, {"REDIS_URL": "redis://cache.example:6380/14"}):
                with patch("redis.Redis.from_url", return_value=sentinel) as factory:
                    reloaded = importlib.reload(shared_redis_client)

            self.assertIs(sentinel, reloaded.redis_client)
            factory.assert_called_once_with(
                "redis://cache.example:6380/14",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        finally:
            importlib.reload(shared_redis_client)


if __name__ == "__main__":
    unittest.main()
