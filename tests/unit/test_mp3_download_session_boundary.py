import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import mp3_video_workflow  # noqa: E402


class _AssertingLock:
    def __init__(self, released):
        self.released = released

    def __enter__(self):
        if not self.released.called:
            raise AssertionError("database session must be released before cache lock wait")
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class Mp3DownloadSessionBoundaryTest(unittest.TestCase):
    def setUp(self):
        mp3_video_workflow._download_result_cache.clear()

    def tearDown(self):
        mp3_video_workflow._download_result_cache.clear()

    def test_cache_lock_wait_happens_after_auth_session_release(self):
        released = Mock()
        mp3_video_workflow._download_result_cache["job-1"] = {
            "expires_at": 200.0,
            "user_id": "user-1",
            "result": {"artifacts": {}},
        }

        with patch.object(
            mp3_video_workflow,
            "_visible_user_ids",
            return_value={"user-1"},
        ), patch.object(
            mp3_video_workflow,
            "release_db_session_quietly",
            released,
        ), patch.object(
            mp3_video_workflow,
            "_download_result_cache_lock",
            _AssertingLock(released),
        ), patch.object(
            mp3_video_workflow.time,
            "monotonic",
            return_value=100.0,
        ), patch.object(
            mp3_video_workflow,
            "_get_owned_task",
        ) as get_owned_task:
            result = mp3_video_workflow._download_result_for_owned_task(
                SimpleNamespace(id="user-1"),
                "job-1",
            )

        self.assertEqual(result, {"artifacts": {}})
        released.assert_called_once_with()
        get_owned_task.assert_not_called()

    def test_cache_miss_releases_query_session_before_file_streaming(self):
        released = Mock()
        task = SimpleNamespace(
            result={"artifacts": {"cover": {"path": "/tmp/cover.jpg"}}},
            user_id="user-1",
        )

        with patch.object(
            mp3_video_workflow,
            "_visible_user_ids",
            return_value={"user-1"},
        ), patch.object(
            mp3_video_workflow,
            "release_db_session_quietly",
            released,
        ), patch.object(
            mp3_video_workflow,
            "_download_result_cache_lock",
            _AssertingLock(released),
        ), patch.object(
            mp3_video_workflow,
            "_get_owned_task",
            return_value=task,
        ), patch.object(
            mp3_video_workflow.time,
            "monotonic",
            return_value=100.0,
        ):
            result = mp3_video_workflow._download_result_for_owned_task(
                SimpleNamespace(id="user-1"),
                "job-1",
            )

        self.assertEqual(result, task.result)
        self.assertIsNot(result, task.result)
        self.assertEqual(released.call_count, 2)


if __name__ == "__main__":
    unittest.main()
