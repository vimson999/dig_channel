import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_audio_workflow as report_audio_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class ReportAudioWorkflowScheme3DependencyTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_full_video_scheme3_implicitly_requests_stick_figure_visual_workflow(self):
        """A scheme-3-only submission must create the visual dependency it renders."""
        saved_pdf = {
            "path": "/tmp/scheme3-source.pdf",
            "filename": "scheme3-source.pdf",
            "bytes": 128,
            "source": "upload",
        }
        with self.app.test_request_context(
            "/api/report-audio-workflows/jobs",
            method="POST",
            data={
                "pdf": (io.BytesIO(b"%PDF-1.4\\n%%EOF\\n"), "scheme3-source.pdf"),
                "video_render_mode": "full_video",
                "vertical_video_enabled": "1",
                "vertical_video_schemes": '["scheme3"]',
                "visual_scheme_keys": "[]",
            },
        ), patch.object(
            report_audio_api.gemini_browser,
            "status_payload",
            return_value={"configured": True},
        ), patch.object(
            report_audio_api.doubao_browser_tts,
            "status_payload",
            return_value={"configured": True},
        ), patch.object(
            report_audio_api,
            "_save_pdf_upload",
            return_value=saved_pdf,
        ), patch.object(
            report_audio_api,
            "apply_async_after_db_release",
            return_value=SimpleNamespace(id="celery-scheme3"),
        ), patch.object(
            report_audio_api,
            "require_report_audio_workers_ready",
            return_value={"ready": True},
        ):
            response = report_audio_api.create_job.__wrapped__(SimpleNamespace(id="user-1"))

        self.assertTrue(response.get_json()["success"])
        task = PublicationTask.query.one()
        self.assertEqual(
            task.params["report_audio_workflow"]["visual_scheme_keys"],
            ["stick_figure"],
        )
