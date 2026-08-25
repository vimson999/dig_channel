import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from apis.v1.zsxq_history_api_clients import bp as history_clients_bp  # noqa: E402
from services.zsxq_history_api_client_service import (  # noqa: E402
    create_history_api_client,
    list_history_api_clients,
    revoke_history_api_client,
    rotate_history_api_client,
)
from utils.zsxq_history_auth import is_valid_service_request  # noqa: E402


class ZsxqHistoryApiClientTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-secret-key",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_client_is_assigned_per_tenant_and_secret_is_not_listed(self):
        first = create_history_api_client(tenant_id="tenant-a", name="侧边栏 A")
        second = create_history_api_client(tenant_id="tenant-b", name="侧边栏 B")

        self.assertTrue(first["app_id"])
        self.assertTrue(first["secret"])
        self.assertNotEqual(first["app_id"], second["app_id"])
        self.assertNotEqual(first["secret"], second["secret"])

        items, total = list_history_api_clients(tenant_id="tenant-a")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["tenant_id"], "tenant-a")
        self.assertNotIn("secret", items[0])
        self.assertNotIn("encrypted_secret", items[0])

    def test_revoke_and_rotate_change_the_credential(self):
        created = create_history_api_client(tenant_id="tenant-a", name="侧边栏")
        rotated = rotate_history_api_client(created["id"])

        self.assertNotEqual(created["secret"], rotated["secret"])
        self.assertEqual(rotated["app_id"], created["app_id"])
        self.assertFalse(revoke_history_api_client(created["id"])["active"])

    def test_signed_request_resolves_secret_from_database(self):
        created = create_history_api_client(tenant_id="tenant-a", name="侧边栏")
        payload = {
            "kind": "mp3",
            "days": 3,
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        signing_string = "\n".join(
            [
                created["app_id"],
                timestamp,
                "POST",
                "/api/v1/zsxq/browser-import/history",
                hashlib.sha256(body).hexdigest(),
            ]
        )
        signature = hmac.new(
            created["secret"].encode("utf-8"),
            signing_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        with self.app.test_request_context(
            "/api/v1/zsxq/browser-import/history",
            method="POST",
            data=body,
            content_type="application/json",
            headers={
                "X-App-Id": created["app_id"],
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
        ):
            self.assertTrue(is_valid_service_request())

    def test_old_secret_fails_after_rotation(self):
        created = create_history_api_client(tenant_id="tenant-a", name="侧边栏")
        rotate_history_api_client(created["id"])
        timestamp = str(int(time.time()))
        body = b'{}'
        signing_string = "\n".join(
            [
                created["app_id"],
                timestamp,
                "POST",
                "/api/v1/zsxq/browser-import/history",
                hashlib.sha256(body).hexdigest(),
            ]
        )
        signature = hmac.new(
            created["secret"].encode("utf-8"),
            signing_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        with self.app.test_request_context(
            "/api/v1/zsxq/browser-import/history",
            method="POST",
            data=body,
            headers={
                "X-App-Id": created["app_id"],
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
        ):
            self.assertFalse(is_valid_service_request())

    def test_admin_management_returns_secret_only_on_create(self):
        self.app.register_blueprint(history_clients_bp)
        admin = type("Admin", (), {"id": 7, "is_admin": True})()
        from unittest.mock import patch

        with patch("utils.auth_utils.get_current_user", return_value=admin):
            created = self.app.test_client().post(
                "/api/v1/zsxq/history-api-clients",
                json={"tenant_id": "tenant-a", "name": "边栏调用方"},
            )
            self.assertEqual(created.status_code, 201)
            created_json = created.get_json()
            self.assertTrue(created_json["credentials"]["secret"])
            client_id = created_json["client"]["id"]

            listed = self.app.test_client().get(
                "/api/v1/zsxq/history-api-clients?tenant_id=tenant-a"
            )
            self.assertEqual(listed.status_code, 200)
            self.assertNotIn("secret", listed.get_json()["data"]["items"][0])

            detail = self.app.test_client().get(
                f"/api/v1/zsxq/history-api-clients/{client_id}"
            )
            self.assertEqual(detail.status_code, 200)
            self.assertNotIn("secret", detail.get_json()["data"]["client"])


if __name__ == "__main__":
    unittest.main()
