import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import report_stick_figure_video_workflow as visual_api  # noqa: E402
from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.report_audio_workflow import FEATURE_NAME as REPORT_AUDIO_FEATURE  # noqa: E402
from services.report_stick_figure_audio_alignment import find_reusable_audio_candidate  # noqa: E402


class ReportStickFigureReportAudioRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
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
        self.tmpdir.cleanup()

    def test_video_stage_failure_with_valid_script_and_wav_can_start_visual_workflow(self):
        """A failed final render must not discard an otherwise reusable audio source."""
        wav_path = Path(self.tmpdir.name) / "complete.wav"
        wav_path.write_bytes(b"RIFF" + b"audio")
        source = PublicationTask(
            status="failed",
            current_step="failed",
            progress=95,
            feature=REPORT_AUDIO_FEATURE,
            user_id="user-1",
            final_script_chars=240,
            report_audio_wav_path=str(wav_path),
            report_audio_pdf_attachment_id="asset-1",
            report_audio_pdf_filename="source.pdf",
            report_audio_pdf_bytes=128,
            report_audio_pdf_topic_title="外资研报",
            params={"feature": REPORT_AUDIO_FEATURE, "user_id": "user-1"},
            result={
                "structured_result": {"final_script_for_tts": "文" * 240},
                "audio": {"wav_path": str(wav_path)},
            },
        )
        db.session.add(source)
        db.session.commit()

        with self.app.test_request_context(json={"visual_scheme_key": "stick_figure"}), patch.object(
            visual_api,
            "enqueue_report_visual_plan_for_material",
            return_value={"job_id": "storyboard-1", "status": "pending"},
        ), patch.object(visual_api, "_latest_storyboard", return_value=None):
            response, status_code = visual_api.create_job_from_report_audio.__wrapped__(
                SimpleNamespace(id="user-1"),
                source.id,
            )

        self.assertEqual(status_code, 201)
        self.assertTrue(response.get_json()["success"])
        parent = PublicationTask.query.filter_by(
            feature=visual_api.FEATURE_NAME,
            source_task_id=source.id,
        ).one()
        self.assertEqual(parent.report_visual_scheme_key, "stick_figure")

    def test_video_stage_failure_with_valid_wav_can_supply_whole_audio_alignment(self):
        """A recoverable parent failure must not hide the existing full WAV from alignment."""
        wav_path = Path(self.tmpdir.name) / "alignment.wav"
        wav_path.write_bytes(b"RIFF" + b"audio")
        source = PublicationTask(
            status="failed",
            current_step="failed",
            progress=95,
            feature=REPORT_AUDIO_FEATURE,
            user_id="user-1",
            final_script_chars=240,
            report_audio_wav_path=str(wav_path),
            report_audio_seconds=45.0,
            report_audio_audio_bytes=wav_path.stat().st_size,
            report_audio_pdf_attachment_id="asset-1",
            report_audio_pdf_filename="source.pdf",
            params={"feature": REPORT_AUDIO_FEATURE, "user_id": "user-1"},
            result={
                "structured_result": {"final_script_for_tts": "文" * 240},
                "audio": {
                    "wav_path": str(wav_path),
                    "seconds": 45.0,
                    "bytes": wav_path.stat().st_size,
                },
            },
        )
        db.session.add(source)
        db.session.commit()

        candidate = find_reusable_audio_candidate(
            material_task_id=source.id,
            visible_user_ids=["user-1"],
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(str(source.id), candidate.task_id)
        self.assertEqual(str(wav_path.resolve()), candidate.audio_path)
