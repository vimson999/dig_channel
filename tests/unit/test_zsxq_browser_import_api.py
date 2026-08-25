import sys
import hashlib
import hmac
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1.zsxq_browser_import import bp  # noqa: E402
from services.zsxq_browser_import_service import IdempotencyConflictError  # noqa: E402


class ZsxqBrowserImportApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def test_preview_returns_validation_report_without_queueing(self):
        report = {"summary": {"files": 1}, "manifests": []}
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.preview_manifests",
            return_value=report,
        ) as preview:
            response = self.client.post(
                "/api/v1/zsxq/browser-import/preview",
                json={"manifest_paths": ["/Users/v9/Downloads/inbox/result.json"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "data": report})
        preview.assert_called_once_with(["/Users/v9/Downloads/inbox/result.json"])

    def test_history_endpoint_returns_server_owned_recent_downloads(self):
        report = {
            "kind": "mp3",
            "days": 3,
            "complete": True,
            "files": [],
            "errors": [],
            "summary": {"unique_downloads": 0},
        }
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.recent_download_history",
            return_value=report,
        ) as history:
            response = self.client.post(
                "/api/v1/zsxq/browser-import/history",
                json={
                    "kind": "mp3",
                    "days": 3,
                    "group_id": "28888112822211",
                    "tab_id": "88844545452542",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "data": report})
        history.assert_called_once_with("mp3", days=3, require_submission_receipt=True)

    def test_history_endpoint_rejects_invalid_or_mismatched_scope(self):
        invalid_payloads = [
            {
                "kind": "video",
                "days": 3,
                "group_id": "28888112822211",
                "tab_id": "88844545452542",
            },
            {
                "kind": "mp3",
                "days": 8,
                "group_id": "28888112822211",
                "tab_id": "88844545452542",
            },
            {
                "kind": "pdf",
                "days": 3,
                "group_id": "28888112822211",
                "tab_id": "88844545452542",
            },
            {
                "kind": "pdf",
                "days": 3,
                "group_id": "wrong-group",
                "tab_id": "51184248544214",
            },
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/v1/zsxq/browser-import/history", json=payload
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["success"])

    def test_history_endpoint_rejects_non_loopback_client(self):
        response = self.client.post(
            "/api/v1/zsxq/browser-import/history",
            json={
                "kind": "pdf",
                "days": 3,
                "group_id": "28888112822211",
                "tab_id": "51184248544214",
            },
            environ_base={"REMOTE_ADDR": "192.168.1.9"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["success"])

    def test_history_endpoint_allows_authenticated_admin_from_public_network(self):
        report = {
            "kind": "pdf",
            "days": 3,
            "complete": True,
            "files": [],
            "errors": [],
            "summary": {"unique_downloads": 0},
        }
        admin = type("Admin", (), {"is_admin": True})()
        with patch(
            "utils.auth_utils.get_current_user",
            return_value=admin,
        ), patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.recent_download_history",
            return_value=report,
        ) as history:
            response = self.client.post(
                "/api/v1/zsxq/browser-import/history",
                json={
                    "kind": "pdf",
                    "days": 3,
                    "group_id": "28888112822211",
                    "tab_id": "51184248544214",
                },
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "data": report})
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        history.assert_called_once_with("pdf", days=3, require_submission_receipt=True)

    def test_public_admin_access_is_limited_to_history_endpoint(self):
        admin = type("Admin", (), {"is_admin": True})()
        with patch("utils.auth_utils.get_current_user", return_value=admin):
            response = self.client.post(
                "/api/v1/zsxq/browser-import/preview",
                json={"manifest_paths": ["/tmp/manifest.json"]},
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["success"])

    def test_history_endpoint_accepts_signed_service_request_without_login(self):
        report = {
            "kind": "mp3",
            "days": 3,
            "complete": True,
            "files": [],
            "errors": [],
            "summary": {"unique_downloads": 0},
        }
        app_id = "zsxq_history_client"
        api_secret = "history-secret-for-test"
        payload = {
            "kind": "mp3",
            "days": 3,
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        signing_string = "\n".join(
            [app_id, timestamp, "POST", "/api/v1/zsxq/browser-import/history", body_hash]
        )
        signature = hmac.new(
            api_secret.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        service_client = SimpleNamespace(scopes=["zsxq.history.read"])
        with patch("utils.auth_utils.get_current_user", return_value=None), patch(
            "utils.zsxq_history_auth.resolve_history_api_client",
            return_value=(service_client, api_secret),
        ), patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.recent_download_history",
            return_value=report,
        ) as history:
            response = self.client.post(
                "/api/v1/zsxq/browser-import/history",
                data=body,
                content_type="application/json",
                headers={
                    "X-App-Id": app_id,
                    "X-Timestamp": timestamp,
                    "X-Signature": signature,
                },
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "data": report})
        history.assert_called_once_with("mp3", days=3, require_submission_receipt=True)

    def test_history_endpoint_rejects_invalid_service_signature(self):
        app_id = "zsxq_history_client"
        api_secret = "history-secret-for-test"
        service_client = SimpleNamespace(scopes=["zsxq.history.read"])
        with patch("utils.auth_utils.get_current_user", return_value=None), patch(
            "utils.zsxq_history_auth.resolve_history_api_client",
            return_value=(service_client, api_secret),
        ), patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.recent_download_history"
        ) as history:
            response = self.client.post(
                "/api/v1/zsxq/browser-import/history",
                json={
                    "kind": "mp3",
                    "days": 3,
                    "group_id": "28888112822211",
                    "tab_id": "88844545452542",
                },
                headers={
                    "X-App-Id": app_id,
                    "X-Timestamp": str(int(time.time())),
                    "X-Signature": "not-a-valid-signature",
                },
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 403)
        history.assert_not_called()

    def test_preview_rejects_missing_manifest_paths(self):
        response = self.client.post("/api/v1/zsxq/browser-import/preview", json={})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])

    def test_non_loopback_client_is_rejected(self):
        response = self.client.post(
            "/api/v1/zsxq/browser-import/preview",
            json={"manifest_paths": ["/Users/v9/Downloads/inbox/result.json"]},
            environ_base={"REMOTE_ADDR": "192.168.1.9"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["success"])

    def test_import_validates_then_queues_celery_task(self):
        report = {"summary": {"files": 2}, "manifests": []}
        async_result = type("AsyncResult", (), {"id": "celery-1"})()
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.preview_manifests",
            return_value=report,
        ) as preview, patch(
            "tasks.zsxq_browser_import.run_browser_import.delay",
            return_value=async_result,
        ) as delay:
            response = self.client.post(
                "/api/v1/zsxq/browser-import",
                json={
                    "manifest_paths": ["/Users/v9/Downloads/inbox/result.json"],
                    "dispatch_clean": False,
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.get_json(),
            {"success": True, "queued": True, "task_id": "celery-1", "preview": report},
        )
        preview.assert_called_once_with(["/Users/v9/Downloads/inbox/result.json"])
        delay.assert_called_once_with(
            ["/Users/v9/Downloads/inbox/result.json"],
            dispatch_clean=False,
            cleaner={},
            auto_publish={},
        )

    def test_inline_manifest_is_persisted_previewed_and_queued(self):
        manifest = {
            "run_id": "sidebar-mp3-20260817T120000+0800",
            "kind": "mp3",
            "group_id": "28888112822211",
            "tab_id": "88844545452542",
            "tab_label": "调研纪要",
            "files": [],
        }
        path = Path("/Users/v9/Downloads/知识星球下载/inbox/server-submit.json")
        report = {"summary": {"files": 0}, "manifests": []}
        prepared = {
            "submission_id": "submission-1",
            "run_id": manifest["run_id"],
            "kind": "mp3",
            "status": "received",
            "manifest_path": str(path),
            "task_id": "celery-inline-1",
            "preview": report,
            "dispatch_options": {
                "dispatch_clean": True,
                "cleaner": {},
                "auto_publish": {"enabled": True},
            },
            "replayed": False,
            "should_dispatch": True,
        }
        queued = {**prepared, "status": "queued", "should_dispatch": False}
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.prepare_inline_submission",
            return_value=prepared,
        ) as prepare, patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.mark_submission_queued",
            return_value=queued,
        ) as mark_queued, patch(
            "tasks.zsxq_browser_import.run_browser_import.apply_async"
        ) as apply_async:
            response = self.client.post(
                "/api/v1/zsxq/browser-import",
                json={
                    "manifest": manifest,
                    "dispatch_clean": True,
                    "auto_publish": {"enabled": True},
                },
                headers={"Idempotency-Key": f"zsxq-sidebar:mp3:{manifest['run_id']}"},
            )

        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertEqual(body["task_id"], "celery-inline-1")
        self.assertEqual(body["manifest_path"], str(path))
        self.assertEqual(body["status"], "queued")
        prepare.assert_called_once_with(
            manifest,
            f"zsxq-sidebar:mp3:{manifest['run_id']}",
            dispatch_clean=True,
            cleaner={},
            auto_publish={"enabled": True},
        )
        apply_async.assert_called_once_with(
            args=[[str(path)]],
            kwargs={
                "dispatch_clean": True,
                "cleaner": {},
                "auto_publish": {"enabled": True},
                "submission_id": "submission-1",
            },
            task_id="celery-inline-1",
        )
        mark_queued.assert_called_once_with("submission-1")

    def test_idempotent_inline_replay_does_not_queue_second_task(self):
        prepared = {
            "submission_id": "submission-1",
            "run_id": "sidebar-pdf-20260817T120000+0800",
            "kind": "pdf",
            "status": "queued",
            "manifest_path": "/Users/v9/Downloads/知识星球下载/inbox/server-submit.json",
            "task_id": "stable-task-id",
            "preview": {"summary": {"files": 0}},
            "replayed": True,
            "should_dispatch": False,
        }
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.prepare_inline_submission",
            return_value=prepared,
        ), patch(
            "tasks.zsxq_browser_import.run_browser_import.apply_async"
        ) as apply_async:
            response = self.client.post(
                "/api/v1/zsxq/browser-import",
                json={
                    "manifest": {
                        "run_id": prepared["run_id"],
                        "kind": "pdf",
                        "files": [],
                    }
                },
                headers={"Idempotency-Key": f"zsxq-sidebar:pdf:{prepared['run_id']}"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["replayed"])
        self.assertEqual(response.get_json()["task_id"], "stable-task-id")
        apply_async.assert_not_called()

    def test_completed_inline_replay_returns_200_without_dispatch(self):
        prepared = {
            "submission_id": "submission-1",
            "run_id": "sidebar-mp3-20260817T120000+0800",
            "kind": "mp3",
            "status": "completed",
            "manifest_path": "/Users/v9/Downloads/知识星球下载/inbox/server-submit.json",
            "task_id": "stable-task-id",
            "preview": {"summary": {"files": 0}},
            "result": {"summary": {"created_attachments": 0}},
            "replayed": True,
            "should_dispatch": False,
        }
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.prepare_inline_submission",
            return_value=prepared,
        ), patch(
            "tasks.zsxq_browser_import.run_browser_import.apply_async"
        ) as apply_async:
            response = self.client.post(
                "/api/v1/zsxq/browser-import",
                json={"manifest": {"run_id": prepared["run_id"], "files": []}},
                headers={"Idempotency-Key": f"zsxq-sidebar:mp3:{prepared['run_id']}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "completed")
        apply_async.assert_not_called()

    def test_inline_idempotency_conflict_returns_409(self):
        with patch(
            "apis.v1.zsxq_browser_import.zsxq_browser_import_service.prepare_inline_submission",
            side_effect=IdempotencyConflictError("payload changed"),
        ):
            response = self.client.post(
                "/api/v1/zsxq/browser-import",
                json={"manifest": {"run_id": "run-1", "files": []}},
                headers={"Idempotency-Key": "zsxq-sidebar:mp3:run-1"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "idempotency_conflict")

    def test_import_rejects_both_or_neither_manifest_input_mode(self):
        payloads = [
            {},
            {
                "manifest": {"run_id": "run-1", "files": []},
                "manifest_paths": ["/Users/v9/Downloads/inbox/result.json"],
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/zsxq/browser-import", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
