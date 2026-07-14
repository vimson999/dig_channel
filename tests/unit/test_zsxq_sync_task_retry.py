import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import zsxq_sync  # noqa: E402
from services.zsxq_sync_service import ZsxqTransientUpstreamError  # noqa: E402


class RetryRaised(Exception):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryRaised()


class ZsxqSyncTaskRetryTest(unittest.TestCase):
    def test_terminal_duplicate_delivery_is_ignored(self):
        persisted_run = SimpleNamespace(status="completed")

        with patch.object(zsxq_sync.db.session, "get", return_value=persisted_run):
            result = zsxq_sync._terminal_delivery_result("run-1", task_id="task-1")

        self.assertEqual(
            result,
            {
                "skipped": True,
                "reason": "duplicate_terminal_delivery",
                "run_id": "run-1",
                "status": "completed",
            },
        )

    def test_non_terminal_delivery_continues(self):
        persisted_run = SimpleNamespace(status="running")

        with patch.object(zsxq_sync.db.session, "get", return_value=persisted_run):
            result = zsxq_sync._terminal_delivery_result("run-1", task_id="task-1")

        self.assertIsNone(result)

    def test_server_closed_connection_is_retryable(self):
        exc = Exception("server closed the connection unexpectedly before or while processing the request")

        self.assertTrue(zsxq_sync._is_retryable_db_error(exc))

    def test_db_retry_count_and_delay_use_zsxq_sync_env(self):
        with patch.dict(
            os.environ,
            {
                "ZSXQ_SYNC_DB_RETRIES": "7",
                "ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS": "30",
            },
            clear=False,
        ):
            self.assertEqual(zsxq_sync._db_retry_count(), 7)
            self.assertEqual(zsxq_sync._db_retry_delay_seconds(0), 30)
            self.assertEqual(zsxq_sync._db_retry_delay_seconds(2), 90)

    def test_1059_upstream_error_is_retryable(self):
        exc = ZsxqTransientUpstreamError("1059", "内部错误")

        self.assertTrue(zsxq_sync._is_retryable_upstream_error(exc))
        self.assertTrue(zsxq_sync._is_retryable_upstream_error(Exception("知识星球接口返回失败 code=1059: 内部错误")))
        self.assertFalse(zsxq_sync._is_retryable_upstream_error(Exception("知识星球接口返回失败 code=1001: 参数错误")))

    def test_upstream_retry_count_and_delay_use_zsxq_sync_env(self):
        with patch.dict(
            os.environ,
            {
                "ZSXQ_SYNC_UPSTREAM_RETRIES": "8",
                "ZSXQ_SYNC_UPSTREAM_RETRY_DELAY_SECONDS": "120",
            },
            clear=False,
        ):
            self.assertEqual(zsxq_sync._upstream_retry_count(), 8)
            self.assertEqual(zsxq_sync._upstream_retry_delay_seconds(0), 120)
            self.assertEqual(zsxq_sync._upstream_retry_delay_seconds(3), 480)

    def test_transient_upstream_error_retries_before_workflow_finalizer(self):
        celery_task = FakeCeleryTask(retries=1)
        exc = ZsxqTransientUpstreamError("1059", "内部错误")

        with patch.object(zsxq_sync, "_upstream_retry_count", return_value=4), \
             patch.object(zsxq_sync, "_upstream_retry_delay_seconds", return_value=123), \
             patch.object(zsxq_sync, "_mark_sync_run_upstream_retrying") as mark_retrying:
            with self.assertRaises(RetryRaised):
                zsxq_sync._maybe_retry_transient_upstream_error(celery_task, "run-1", exc)

        mark_retrying.assert_called_once_with(
            "run-1",
            exc,
            celery_task,
            countdown=123,
            retry_count=4,
        )
        self.assertEqual(celery_task.retry_kwargs["countdown"], 123)
        self.assertEqual(celery_task.retry_kwargs["max_retries"], 4)

    def test_transient_upstream_error_falls_through_after_retry_limit(self):
        celery_task = FakeCeleryTask(retries=4)
        exc = ZsxqTransientUpstreamError("1059", "内部错误")

        with patch.object(zsxq_sync, "_upstream_retry_count", return_value=4), \
             patch.object(zsxq_sync, "_mark_sync_run_upstream_retrying") as mark_retrying:
            self.assertFalse(zsxq_sync._maybe_retry_transient_upstream_error(celery_task, "run-1", exc))

        mark_retrying.assert_not_called()
        self.assertIsNone(celery_task.retry_kwargs)


if __name__ == "__main__":
    unittest.main()
