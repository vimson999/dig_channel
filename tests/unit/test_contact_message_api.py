import os
import sys
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "src", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import create_app  # noqa: E402
from config.production import ProductionConfig  # noqa: E402
from models import db  # noqa: E402


class ContactApiTestConfig(ProductionConfig):
    TESTING = True
    SECRET_KEY = "s" * 48
    JWT_SECRET_KEY = "j" * 48
    LOGIN_PORTAL_TOKEN = "p" * 48
    CORS_ORIGINS = ["https://private.example.com"]
    RATELIMIT_STORAGE_URL = "redis://redis:6379/0"
    RATELIMIT_STORAGE_URI = RATELIMIT_STORAGE_URL
    RATELIMIT_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ContactMessageApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(ContactApiTestConfig)
        cls.context = cls.app.app_context()
        cls.context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.context.pop()

    def test_valid_local_message_is_accepted_for_async_delivery(self):
        queued = {
            "queued": True,
            "durable": True,
            "deduplicated": False,
            "delivery_id": "delivery-123",
            "celery_task_id": "celery-123",
        }
        with patch.dict(
            "os.environ",
            {
                "CONTACT_API_KEY": "k" * 48,
                "CONTACT_TO_EMAIL": "owner@example.test",
            },
            clear=False,
        ), patch(
            "services.workflow_notification_service.WorkflowNotificationService.enqueue_contact_message",
            return_value=queued,
            create=True,
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "name": "张三",
                    "email": "visitor@example.test",
                    "message": "我想了解你们的服务。",
                    "source": "example.test",
                    "idempotency_key": "contact-001",
                },
                headers={"Authorization": f"Bearer {'k' * 48}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.get_json(),
            {
                "success": True,
                "data": {
                    "queued": True,
                    "deduplicated": False,
                    "delivery_id": "delivery-123",
                },
            },
        )

    def test_message_is_persisted_before_async_dispatch(self):
        class AsyncResult:
            id = "celery-real-123"

        with patch.dict(
            "os.environ",
            {
                "CONTACT_API_KEY": "k" * 48,
                "CONTACT_TO_EMAIL": "owner@example.test",
                "WORKFLOW_NOTIFY_PROVIDER": "email",
            },
            clear=False,
        ), patch(
            "services.workflow_notification_service.apply_async_after_db_release",
            return_value=AsyncResult(),
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "name": "李四",
                    "email": "visitor2@example.test",
                    "message": "请联系我。",
                    "source": "landing.example.test",
                    "idempotency_key": "contact-real-001",
                },
                headers={"Authorization": f"Bearer {'k' * 48}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 202)
        self.assertTrue(payload["data"]["queued"])
        self.assertTrue(payload["data"]["delivery_id"])

    def test_invalid_visitor_email_is_rejected_without_enqueueing(self):
        with patch.dict(
            "os.environ",
            {
                "CONTACT_API_KEY": "k" * 48,
                "CONTACT_TO_EMAIL": "owner@example.test",
            },
            clear=False,
        ), patch(
            "services.workflow_notification_service.WorkflowNotificationService.enqueue_contact_message",
            side_effect=AssertionError("invalid input must not reach the outbox"),
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "name": "访客",
                    "email": "not-an-email",
                    "message": "测试留言",
                    "source": "example.test",
                    "idempotency_key": "contact-invalid-001",
                },
                headers={"Authorization": f"Bearer {'k' * 48}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["field"], "email")

    def test_remote_request_cannot_use_the_server_local_endpoint(self):
        with patch.dict(
            "os.environ",
            {"CONTACT_API_KEY": "k" * 48},
            clear=False,
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "email": "visitor@example.test",
                    "message": "测试留言",
                    "idempotency_key": "contact-remote-001",
                },
                headers={"Authorization": f"Bearer {'k' * 48}"},
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 404)

    def test_local_nginx_peer_is_allowed_even_when_proxyfix_exposes_visitor_ip(self):
        queued = {
            "queued": True,
            "deduplicated": False,
            "delivery_id": "delivery-proxied-123",
        }
        with patch.dict(
            "os.environ",
            {
                "CONTACT_API_KEY": "k" * 48,
                "CONTACT_TO_EMAIL": "owner@example.test",
            },
            clear=False,
        ), patch(
            "services.workflow_notification_service.WorkflowNotificationService.enqueue_contact_message",
            return_value=queued,
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "email": "visitor@example.test",
                    "message": "由本机 Nginx 转发的留言",
                    "idempotency_key": "contact-proxied-001",
                },
                headers={
                    "Authorization": f"Bearer {'k' * 48}",
                    "X-Forwarded-For": "203.0.113.10",
                },
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 202)

    def test_remote_peer_cannot_spoof_loopback_with_forwarded_for(self):
        with patch.dict(
            "os.environ",
            {
                "CONTACT_API_KEY": "k" * 48,
                "CONTACT_TO_EMAIL": "owner@example.test",
            },
            clear=False,
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "email": "visitor@example.test",
                    "message": "不应被接受",
                    "idempotency_key": "contact-spoofed-001",
                },
                headers={
                    "Authorization": f"Bearer {'k' * 48}",
                    "X-Forwarded-For": "127.0.0.1",
                },
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 404)

    def test_short_contact_api_key_is_rejected(self):
        with patch.dict(
            "os.environ",
            {"CONTACT_API_KEY": "too-short"},
            clear=False,
        ):
            response = self.app.test_client().post(
                "/api/v1/contact-messages",
                json={
                    "email": "visitor@example.test",
                    "message": "测试留言",
                    "idempotency_key": "contact-short-key-001",
                },
                headers={"Authorization": "Bearer too-short"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 404)

    def test_repeated_idempotency_key_reuses_the_delivery(self):
        class AsyncResult:
            id = "celery-dedupe-123"

        request_json = {
            "name": "王五",
            "email": "visitor3@example.test",
            "message": "同一条留言重试。",
            "source": "landing.example.test",
            "idempotency_key": "contact-dedupe-001",
        }
        with patch.dict(
            "os.environ",
            {
                "CONTACT_API_KEY": "k" * 48,
                "CONTACT_TO_EMAIL": "owner@example.test",
                "WORKFLOW_NOTIFY_PROVIDER": "email",
            },
            clear=False,
        ), patch(
            "services.workflow_notification_service.apply_async_after_db_release",
            return_value=AsyncResult(),
        ):
            first = self.app.test_client().post(
                "/api/v1/contact-messages",
                json=request_json,
                headers={"Authorization": f"Bearer {'k' * 48}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            second = self.app.test_client().post(
                "/api/v1/contact-messages",
                json=request_json,
                headers={"Authorization": f"Bearer {'k' * 48}"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        first_data = first.get_json()["data"]
        second_data = second.get_json()["data"]
        self.assertEqual(first_data["delivery_id"], second_data["delivery_id"])
        self.assertFalse(first_data["deduplicated"])
        self.assertTrue(second_data["deduplicated"])


if __name__ == "__main__":
    unittest.main()
