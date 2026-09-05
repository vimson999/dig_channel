import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, request


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import User, db  # noqa: E402
from services.zsxq_analysis_service import create_analysis_run  # noqa: E402
from apis.v1 import zsxq_analysis as zsxq_analysis_api  # noqa: E402
from apis.v1.zsxq_analysis import bp  # noqa: E402


def _source(kind="pdf", name="AI大模型资料.pdf"):
    return json.dumps(
        {
            "schema": "zsxq-list-export/v1",
            "kind": kind,
            "exportedAt": "2026-09-02T02:31:01.385Z",
            "summary": {"total": 1},
            "items": [
                {
                    "downloadCount": 7,
                    "duration": 60 if kind == "audio" else 0,
                    "fileId": f"{kind}-1",
                    "fileSize": 100,
                    "groupName": "测试分组",
                    "name": name,
                    "status": "done",
                    "topicId": f"{kind}-topic",
                    "uploadTime": "2026-09-02T10:00:00+08:00",
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


class ZsxqAnalysisApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            ZSXQ_ANALYSIS_ARTIFACT_ROOT=self.temp_dir.name,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add_all(
            [
                User(
                    username="api-one",
                    email="api-one@example.com",
                    password_hash="x",
                ),
                User(
                    username="api-two",
                    email="api-two@example.com",
                    password_hash="x",
                ),
            ]
        )
        db.session.commit()
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def test_all_endpoints_require_login(self):
        with patch("utils.auth_utils.get_current_user", return_value=None):
            response = self.client.get("/api/v1/zsxq-analysis/runs")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()["success"])

    def test_history_list_is_user_and_kind_scoped_and_narrow(self):
        pdf_one = create_analysis_run(
            user_id=1, source_bytes=_source("pdf"), source_filename="one.json"
        )
        create_analysis_run(
            user_id=2,
            source_bytes=_source("pdf", "另一个资料.pdf"),
            source_filename="two.json",
        )
        create_analysis_run(
            user_id=1,
            source_bytes=_source("audio", "闭门会.mp3"),
            source_filename="audio.json",
        )

        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=1),
        ):
            response = self.client.get("/api/v1/zsxq-analysis/runs?kind=pdf")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["items"][0]["id"], pdf_one.id)
        self.assertEqual(payload["items"][0]["kind"], "pdf")
        self.assertNotIn("source_artifact_path", payload["items"][0])
        self.assertNotIn("items", payload["items"][0])
        self.assertNotIn("keywords", payload["items"][0])

    def test_history_list_requires_one_explicit_kind(self):
        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=1),
        ):
            response = self.client.get("/api/v1/zsxq-analysis/runs")

        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.get_json()["error"]["message"])

    def test_other_users_cannot_read_detail_items_or_source(self):
        other_run = create_analysis_run(
            user_id=2,
            source_bytes=_source("pdf"),
            source_filename="other.json",
        )

        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=1),
        ):
            detail = self.client.get(
                f"/api/v1/zsxq-analysis/runs/{other_run.id}"
            )
            items = self.client.get(
                f"/api/v1/zsxq-analysis/runs/{other_run.id}/items"
            )
            source = self.client.get(
                f"/api/v1/zsxq-analysis/runs/{other_run.id}/source"
            )

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(items.status_code, 404)
        self.assertEqual(source.status_code, 404)

    def test_upload_persists_one_run_and_source_endpoint_returns_exact_bytes(
        self,
    ):
        source = _source("pdf")
        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=1),
        ):
            response = self.client.post(
                "/api/v1/zsxq-analysis/runs",
                data={
                    "kind": "pdf",
                    "file": (io.BytesIO(source), "upload.json"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 201)
        run_id = response.get_json()["data"]["run"]["id"]
        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=1),
        ):
            source_response = self.client.get(
                f"/api/v1/zsxq-analysis/runs/{run_id}/source"
            )
        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(source_response.data, source)

    def test_upload_rejects_cross_tab_kind(self):
        with patch(
            "utils.auth_utils.get_current_user",
            return_value=SimpleNamespace(id=1),
        ):
            response = self.client.post(
                "/api/v1/zsxq-analysis/runs",
                data={
                    "kind": "audio",
                    "file": (io.BytesIO(_source("pdf")), "pdf.json"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tab", response.get_json()["error"]["message"])

    def test_upload_releases_auth_session_before_reading_file_stream(self):
        observed_transaction_states = []
        with self.app.test_request_context(
            "/api/v1/zsxq-analysis/runs",
            method="POST",
            data={
                "kind": "pdf",
                "file": (io.BytesIO(_source("pdf")), "upload.json"),
            },
            content_type="multipart/form-data",
        ):
            upload = request.files["file"]
            source_stream = upload.stream

            class TrackingStream:
                def read(self, *args, **kwargs):
                    observed_transaction_states.append(
                        db.session().in_transaction()
                    )
                    return source_stream.read(*args, **kwargs)

            upload.stream = TrackingStream()
            current_user = db.session.get(User, 1)
            self.assertTrue(db.session().in_transaction())

            response = self.app.make_response(
                zsxq_analysis_api.create_run.__wrapped__(current_user)
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(observed_transaction_states, [False])


if __name__ == "__main__":
    unittest.main()
