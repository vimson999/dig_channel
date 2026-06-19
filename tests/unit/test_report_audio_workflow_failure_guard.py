import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import report_audio_workflow  # noqa: E402


class RetryRaised(Exception):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryRaised()


class ReportAudioWorkflowFailureGuardTest(unittest.TestCase):
    def test_retryable_db_error_retries_before_failure_email(self):
        celery_task = FakeCeleryTask(retries=0)

        with patch.object(report_audio_workflow, "_db_retry_count", return_value=3), \
             patch.object(report_audio_workflow, "_db_retry_delay_seconds", return_value=9), \
             patch.object(report_audio_workflow, "_mark_report_audio_failed_safely") as mark_failed, \
             patch.object(report_audio_workflow, "_send_direct_report_audio_failure_email") as send_email:
            with self.assertRaises(RetryRaised):
                report_audio_workflow._handle_report_audio_task_failure(
                    celery_task,
                    "task-1",
                    RuntimeError('connection to server at "db", port 5432 failed: timeout expired'),
                )

        self.assertEqual(celery_task.retry_kwargs["countdown"], 9)
        self.assertEqual(celery_task.retry_kwargs["max_retries"], 3)
        mark_failed.assert_not_called()
        send_email.assert_not_called()

    def test_final_failure_sends_direct_email_when_notification_not_queued(self):
        celery_task = FakeCeleryTask(retries=3)
        details = {
            "persisted": False,
            "notification_queued": False,
            "persist_error": "db still unavailable",
        }

        with patch.object(report_audio_workflow, "_db_retry_count", return_value=3), \
             patch.object(report_audio_workflow, "_mark_report_audio_failed_safely", return_value=details) as mark_failed, \
             patch.object(report_audio_workflow, "_send_direct_report_audio_failure_email") as send_email:
            result = report_audio_workflow._handle_report_audio_task_failure(
                celery_task,
                "task-1",
                RuntimeError('connection to server at "db", port 5432 failed: timeout expired'),
            )

        self.assertFalse(result)
        mark_failed.assert_called_once_with("task-1", ANY)
        send_email.assert_called_once_with(
            task_id="task-1",
            error=ANY,
            persisted=False,
            notification_queued=False,
            persist_error="db still unavailable",
        )


if __name__ == "__main__":
    unittest.main()
