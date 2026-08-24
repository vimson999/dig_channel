import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import publisher  # noqa: E402


class RetryRaised(Exception):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryRaised()


class PublisherDbRetryTest(unittest.TestCase):
    def test_terminal_continuations_run_finalization_and_parent_wakeup(self):
        with patch(
            "services.report_audio_finalization.reconcile_finalization_for_publisher"
        ) as reconcile, patch(
            "services.report_audio_parent_wakeup.wake_report_audio_parent_for_child"
        ) as wake:
            publisher._notify_terminal_continuations("publisher-1")

        reconcile.assert_called_once_with("publisher-1")
        wake.assert_called_once_with("publisher-1")

    def test_finalization_failure_does_not_block_parent_wakeup(self):
        with patch(
            "services.report_audio_finalization.reconcile_finalization_for_publisher",
            side_effect=RuntimeError("coordinator unavailable"),
        ), patch(
            "services.report_audio_parent_wakeup.wake_report_audio_parent_for_child"
        ) as wake, patch.object(publisher.db.session, "rollback") as rollback:
            publisher._notify_terminal_continuations("publisher-1")

        rollback.assert_called_once_with()
        wake.assert_called_once_with("publisher-1")

    def test_read_only_initial_write_retries_without_business_failure(self):
        celery_task = FakeCeleryTask(retries=0)
        error = RuntimeError("cannot execute UPDATE in a read-only transaction")

        with patch.dict(
            publisher.os.environ,
            {
                publisher.TASK_DB_RETRIES_ENV_NAME: "12",
                publisher.TASK_DB_RETRY_DELAY_ENV_NAME: "75",
            },
        ), patch.object(
            publisher,
            "release_db_session_quietly",
        ) as release_session:
            with self.assertRaises(RetryRaised):
                publisher._retry_initial_task_db_write(
                    celery_task,
                    "task-1",
                    error,
                )

        release_session.assert_called_once_with()
        self.assertIs(celery_task.retry_kwargs["exc"], error)
        self.assertEqual(75, celery_task.retry_kwargs["countdown"])
        self.assertEqual(12, celery_task.retry_kwargs["max_retries"])

    def test_non_database_error_is_not_retried(self):
        celery_task = FakeCeleryTask()

        result = publisher._retry_initial_task_db_write(
            celery_task,
            "task-1",
            ValueError("bad media input"),
        )

        self.assertFalse(result)
        self.assertIsNone(celery_task.retry_kwargs)

    def test_exhausted_database_retry_returns_to_failure_handler(self):
        celery_task = FakeCeleryTask(retries=3)

        with patch.dict(
            publisher.os.environ,
            {publisher.TASK_DB_RETRIES_ENV_NAME: "3"},
        ):
            result = publisher._retry_initial_task_db_write(
                celery_task,
                "task-1",
                RuntimeError("read-only transaction"),
            )

        self.assertFalse(result)
        self.assertIsNone(celery_task.retry_kwargs)


if __name__ == "__main__":
    unittest.main()
