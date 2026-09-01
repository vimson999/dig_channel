import sys
import unittest
import smtplib
import os
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.workflow_notification_service import WorkflowNotificationService  # noqa: E402
from services import workflow_notification_outbox  # noqa: E402
from tasks import workflow_notify  # noqa: E402


class WorkflowNotificationRetryPolicyTest(unittest.TestCase):

    def test_notification_task_uses_db_only_app_context(self):
        with patch.object(
            workflow_notify,
            "create_app",
            side_effect=RuntimeError("stop after factory call"),
        ) as create_app:
            with self.assertRaisesRegex(RuntimeError, "stop after factory call"):
                workflow_notify.send_workflow_notification.run("email")

        create_app.assert_called_once_with(register_apis=False)

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

    def test_provider_breaker_uses_one_shared_exponential_delay_with_jitter(self):
        with patch.object(
            workflow_notification_outbox.random,
            "uniform",
            return_value=7.5,
        ):
            self.assertEqual(
                67.5,
                workflow_notification_outbox._breaker_delay_seconds(1),
            )
            self.assertEqual(
                127.5,
                workflow_notification_outbox._breaker_delay_seconds(2),
            )

    def test_provider_retry_countdown_rounds_up_past_open_until(self):
        now = datetime(2026, 8, 29, 8, 0, 0)
        target = now + timedelta(seconds=60, microseconds=1)
        fake_datetime = unittest.mock.Mock()
        fake_datetime.fromisoformat.return_value = target
        fake_datetime.utcnow.return_value = now

        with patch.object(workflow_notify, "datetime", fake_datetime):
            self.assertEqual(61, workflow_notify._seconds_until(target.isoformat()))

    def test_delivery_lease_covers_smtp_timeout_and_protocol_budget(self):
        settings = {
            "timeout": 12,
            "port": 465,
            "fallback_starttls": True,
        }

        self.assertEqual(126, workflow_notify._delivery_lease_seconds(settings))
        settings["fallback_starttls"] = False
        self.assertEqual(78, workflow_notify._delivery_lease_seconds(settings))
        settings["timeout"] = 600
        self.assertEqual(900, workflow_notify._delivery_lease_seconds(settings))

    def test_transient_disconnect_does_not_try_second_smtp_protocol(self):
        service = WorkflowNotificationService()
        settings = {
            "host": "smtp.example.test",
            "port": 465,
            "user": "sender@example.test",
            "password": "secret",
            "from_addr": "sender@example.test",
            "from_name": "Test",
            "to": "owner@example.test",
            "timeout": 1,
            "retry_attempts": 1,
            "retry_delay_seconds": 0,
            "fallback_starttls": True,
            "use_ssl": True,
            "use_tls": False,
        }

        with patch.object(
            service,
            "_send_email",
            side_effect=smtplib.SMTPServerDisconnected("connection closed"),
        ) as send:
            with self.assertRaises(smtplib.SMTPServerDisconnected):
                service._send_email_with_retry(settings, "subject", "body")

        send.assert_called_once()

    def test_smtp_authentication_and_550_recipient_errors_are_permanent(self):
        service = WorkflowNotificationService()

        self.assertFalse(
            service._is_transient_email_error(
                smtplib.SMTPAuthenticationError(535, b"authentication failed")
            )
        )
        self.assertFalse(
            service._is_transient_email_error(
                smtplib.SMTPRecipientsRefused(
                    {"owner@example.test": (550, b"mailbox unavailable")}
                )
            )
        )

    def test_smtp_4xx_response_is_transient_but_5xx_data_error_is_permanent(self):
        service = WorkflowNotificationService()

        self.assertTrue(
            service._is_transient_email_error(
                smtplib.SMTPDataError(451, b"temporary local problem")
            )
        )
        self.assertFalse(
            service._is_transient_email_error(
                smtplib.SMTPDataError(554, b"message rejected")
            )
        )

    def test_durable_workflow_send_clamps_inner_smtp_attempts_to_one(self):
        service = WorkflowNotificationService()
        with patch.dict(
            os.environ,
            {
                "WORKFLOW_NOTIFY_PROVIDER": "email",
                "WORKFLOW_NOTIFY_LEVEL": "milestone",
                "WORKFLOW_EMAIL_USER": "sender@example.test",
                "WORKFLOW_EMAIL_PASSWORD": "secret",
                "WORKFLOW_NOTIFY_TO": "owner@example.test",
                "WORKFLOW_EMAIL_RETRY_ATTEMPTS": "3",
                "WORKFLOW_EMAIL_RETRY_DELAY_SECONDS": "0",
                "WORKFLOW_EMAIL_FALLBACK_STARTTLS": "false",
            },
            clear=False,
        ), patch.object(
            service,
            "_send_email",
            side_effect=smtplib.SMTPServerDisconnected("provider offline"),
        ) as send:
            with self.assertRaises(smtplib.SMTPServerDisconnected):
                service.send(
                    {
                        "event_type": "workflow_completed",
                        "workflow": {"id": "workflow-1"},
                    }
                )

        send.assert_called_once()

    def test_task_source_has_no_per_event_celery_retry_budget(self):
        source = Path(workflow_notify.__file__).read_text(encoding="utf-8")

        self.assertNotIn("self.retry(", source)
        self.assertIn("acquire_delivery", source)
        self.assertIn("fail_delivery", source)

    def test_reconciler_dispatches_each_due_provider_after_snapshot(self):
        with patch(
            "services.workflow_notification_outbox.due_provider_drains",
            return_value=[{"provider": "email", "due_count": 20}],
        ) as due, patch.object(
            workflow_notify,
            "_schedule_provider_drain",
        ) as schedule:
            result = workflow_notify.reconcile_workflow_notifications_once()

        due.assert_called_once_with()
        schedule.assert_called_once_with("email")
        self.assertEqual(1, result["scheduled"])
        self.assertEqual(20, result["due_count"])

    def test_watchdog_loop_invokes_notification_reconciler(self):
        loop = (
            Path(workflow_notify.__file__).resolve().parents[3]
            / "scripts"
            / "dev"
            / "celery_watchdog_loop.py"
        ).read_text(encoding="utf-8")

        self.assertIn("reconcile_workflow_notifications_once", loop)

    def test_watchdog_notification_repair_failure_isolated_from_service_healing(self):
        path = (
            Path(workflow_notify.__file__).resolve().parents[3]
            / "scripts"
            / "dev"
            / "celery_watchdog_loop.py"
        )
        spec = importlib.util.spec_from_file_location("celery_watchdog_loop", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module._reconcile_notifications_safely(
            unittest.mock.Mock(side_effect=RuntimeError("outbox unavailable"))
        )

        self.assertEqual(0, result["scheduled"])
        self.assertEqual("outbox unavailable", result["error"])


if __name__ == "__main__":
    unittest.main()
