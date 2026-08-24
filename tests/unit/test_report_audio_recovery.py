import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import report_audio_recovery  # noqa: E402


class ReportAudioRecoveryTest(unittest.TestCase):
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

    def _failed(self, error, *, age_minutes=5):
        failed_at = datetime.utcnow() - timedelta(minutes=age_minutes)
        task = PublicationTask(
            status="failed",
            current_step="failed",
            progress=48,
            params={
                "feature": "report_pdf_audio_workflow",
                "report_audio_workflow": {},
            },
            error_message=error,
            created_at=failed_at,
            completed_at=failed_at,
            updated_at=failed_at,
            result={"error": error, "structured_result": {"title": "checkpoint"}},
        )
        db.session.add(task)
        db.session.commit()
        return str(task.id)

    def test_transient_terminal_failure_is_requeued_from_existing_checkpoints(self):
        task_id = self._failed("等待 Gemini 回复超时")
        submit = {
            "async_result": SimpleNamespace(id="recovery-celery-id"),
            "deferred": False,
        }
        with patch.object(
            report_audio_recovery,
            "maybe_guarded_apply_async",
            return_value=submit,
        ) as enqueue:
            result = report_audio_recovery.repair_failed_report_audio_workflows_once()

        self.assertEqual(result["requeued"], 1)
        task = db.session.get(PublicationTask, task_id)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "queued")
        self.assertEqual(task.celery_task_id, "recovery-celery-id")
        self.assertIsNone(task.error_message)
        self.assertEqual(task.result["automatic_recovery"]["count"], 1)
        self.assertEqual(task.result["structured_result"]["title"], "checkpoint")
        enqueue.assert_called_once()

    def test_captcha_and_login_failures_are_not_placed_in_an_automatic_loop(self):
        captcha_id = self._failed("豆包浏览器朗读被人机验证阻塞")
        login_id = self._failed("Gemini 登录态失效，需要先登录")
        with patch.object(
            report_audio_recovery,
            "maybe_guarded_apply_async",
        ) as enqueue:
            result = report_audio_recovery.repair_failed_report_audio_workflows_once()

        self.assertEqual(result["requeued"], 0)
        self.assertEqual(db.session.get(PublicationTask, captcha_id).status, "failed")
        self.assertEqual(db.session.get(PublicationTask, login_id).status, "failed")
        enqueue.assert_not_called()

    def test_recovery_is_bounded_per_parent(self):
        task_id = self._failed("Page.goto: net::ERR_CONNECTION_CLOSED")
        task = db.session.get(PublicationTask, task_id)
        task.result = {
            **task.result,
            "automatic_recovery": {"count": report_audio_recovery.DEFAULT_MAX_ATTEMPTS},
        }
        db.session.commit()
        with patch.object(
            report_audio_recovery,
            "maybe_guarded_apply_async",
        ) as enqueue:
            result = report_audio_recovery.repair_failed_report_audio_workflows_once()

        self.assertEqual(result["requeued"], 0)
        self.assertEqual(db.session.get(PublicationTask, task_id).status, "failed")
        enqueue.assert_not_called()

    def test_historical_failures_are_not_resurrected(self):
        task_id = self._failed("等待 Gemini 回复超时", age_minutes=2 * 24 * 60)
        with patch.object(
            report_audio_recovery,
            "maybe_guarded_apply_async",
        ) as enqueue:
            result = report_audio_recovery.repair_failed_report_audio_workflows_once()

        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["requeued"], 0)
        self.assertEqual(db.session.get(PublicationTask, task_id).status, "failed")
        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
