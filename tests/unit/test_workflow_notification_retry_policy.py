import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.workflow_notification_service import WorkflowNotificationService  # noqa: E402
from tasks import workflow_notify  # noqa: E402


class WorkflowNotificationRetryPolicyTest(unittest.TestCase):
    def test_service_defaults_to_one_smtp_attempt(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = WorkflowNotificationService().email_settings()

        self.assertEqual(1, settings["retry_attempts"])

    def test_task_retry_uses_exponential_backoff_with_jitter(self):
        with patch.object(workflow_notify.random, "uniform", return_value=7.5):
            self.assertEqual(37.5, workflow_notify._notification_retry_delay(0))
            self.assertEqual(67.5, workflow_notify._notification_retry_delay(1))
            self.assertEqual(127.5, workflow_notify._notification_retry_delay(2))

    def test_task_retry_delay_is_capped(self):
        with patch.object(workflow_notify.random, "uniform", return_value=30.0):
            self.assertEqual(330.0, workflow_notify._notification_retry_delay(99))


if __name__ == "__main__":
    unittest.main()
