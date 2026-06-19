import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import zsxq_publish  # noqa: E402


class RetryRaised(Exception):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryRaised()


class ZsxqPublishFailureGuardTest(unittest.TestCase):
    def test_retryable_db_error_retries_before_failure_email(self):
        celery_task = FakeCeleryTask(retries=0)

        with patch.object(zsxq_publish, "_run_publish_clean_output_task_impl", side_effect=RuntimeError('connection to server at "db", port 5432 failed: timeout expired')), \
             patch.object(zsxq_publish, "_db_retry_count", return_value=3), \
             patch.object(zsxq_publish, "_db_retry_delay_seconds", return_value=9), \
             patch.object(zsxq_publish, "_mark_publish_task_failed_safely") as mark_failed, \
             patch.object(zsxq_publish, "_send_direct_publish_failure_email") as send_email:
            with self.assertRaises(RetryRaised):
                zsxq_publish._run_publish_task_with_guard(celery_task, "task-1")

        self.assertEqual(celery_task.retry_kwargs["countdown"], 9)
        self.assertEqual(celery_task.retry_kwargs["max_retries"], 3)
        mark_failed.assert_not_called()
        send_email.assert_not_called()

    def test_final_failure_sends_direct_email_when_workflow_not_notified(self):
        celery_task = FakeCeleryTask(retries=3)
        details = {
            "workflow_run_id": "workflow-1",
            "persisted": False,
            "workflow_notified": False,
            "persist_error": "db still unavailable",
        }

        with patch.object(zsxq_publish, "_run_publish_clean_output_task_impl", side_effect=RuntimeError('connection to server at "db", port 5432 failed: timeout expired')), \
             patch.object(zsxq_publish, "_db_retry_count", return_value=3), \
             patch.object(zsxq_publish, "_mark_publish_task_failed_safely", return_value=details) as mark_failed, \
             patch.object(zsxq_publish, "_send_direct_publish_failure_email") as send_email:
            result = zsxq_publish._run_publish_task_with_guard(celery_task, "task-1")

        self.assertFalse(result)
        mark_failed.assert_called_once_with("task-1", ANY)
        send_email.assert_called_once_with(
            task_id="task-1",
            error=ANY,
            workflow_run_id="workflow-1",
            persisted=False,
            workflow_notified=False,
            persist_error="db still unavailable",
        )


if __name__ == "__main__":
    unittest.main()
