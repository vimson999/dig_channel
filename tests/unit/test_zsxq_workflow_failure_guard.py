import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import zsxq_workflow  # noqa: E402


class RetryRaised(Exception):
    pass


class FakeCeleryTask:
    def __init__(self, retries=0):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        raise RetryRaised()


class ZsxqWorkflowFailureGuardTest(unittest.TestCase):
    def test_retryable_db_error_retries_before_failure_email(self):
        celery_task = FakeCeleryTask(retries=0)

        with patch.object(zsxq_workflow, "_db_retry_count", return_value=3), \
             patch.object(zsxq_workflow, "_db_retry_delay_seconds", return_value=9), \
             patch("models.db.session.rollback") as rollback, \
             patch.object(zsxq_workflow, "_send_direct_workflow_failure_email") as send_email:
            with self.assertRaises(RetryRaised):
                zsxq_workflow._handle_task_failure(
                    celery_task,
                    RuntimeError('connection to server at "db", port 5432 failed: timeout expired'),
                    workflow_run_id="workflow-1",
                    stage="sync",
                    final_message="同步阶段异常",
                )

        rollback.assert_called_once()
        self.assertEqual(celery_task.retry_kwargs["countdown"], 9)
        self.assertEqual(celery_task.retry_kwargs["max_retries"], 3)
        send_email.assert_not_called()

    def test_non_retryable_error_marks_failed_and_sends_direct_email(self):
        celery_task = FakeCeleryTask(retries=0)
        error = RuntimeError("unexpected workflow failure")

        with patch("models.db.session.rollback"), \
             patch("services.zsxq_workflow_service.zsxq_workflow_service.mark_failed_by_id", return_value={"id": "workflow-1"}) as mark_failed, \
             patch.object(zsxq_workflow, "_send_direct_workflow_failure_email") as send_email:
            with self.assertRaises(RuntimeError):
                zsxq_workflow._handle_task_failure(
                    celery_task,
                    error,
                    workflow_run_id="workflow-1",
                    stage="workflow",
                    final_message="工作流推进阶段异常",
                )

        mark_failed.assert_called_once_with("workflow-1", "工作流推进阶段异常", stage="workflow")
        send_email.assert_called_once_with(
            workflow_run_id="workflow-1",
            stage="workflow",
            final_message="工作流推进阶段异常",
            error=error,
            retryable_db_error=False,
            marked_failed=True,
            mark_error=None,
        )

    def test_mark_failed_failure_still_sends_direct_email(self):
        celery_task = FakeCeleryTask(retries=3)
        error = RuntimeError('connection to server at "db", port 5432 failed: timeout expired')

        with patch.object(zsxq_workflow, "_db_retry_count", return_value=3), \
             patch("models.db.session.rollback"), \
             patch("services.zsxq_workflow_service.zsxq_workflow_service.mark_failed_by_id", side_effect=RuntimeError("db still unavailable")), \
             patch.object(zsxq_workflow, "_send_direct_workflow_failure_email") as send_email:
            with self.assertRaises(RuntimeError):
                zsxq_workflow._handle_task_failure(
                    celery_task,
                    error,
                    workflow_run_id="workflow-1",
                    stage="sync",
                    final_message="同步阶段异常",
                )

        send_email.assert_called_once_with(
            workflow_run_id="workflow-1",
            stage="sync",
            final_message="同步阶段异常",
            error=error,
            retryable_db_error=True,
            marked_failed=False,
            mark_error=ANY,
        )


if __name__ == "__main__":
    unittest.main()
