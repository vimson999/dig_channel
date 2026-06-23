import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import runtime_guard  # noqa: E402


class RuntimeGuardTest(unittest.TestCase):
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
        runtime_guard._DB_PRESSURE_CACHE["expires_at"] = 0.0
        runtime_guard._DB_PRESSURE_CACHE["snapshot"] = None

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _add_task(self, status, feature="report_pdf_audio_workflow"):
        task = PublicationTask(
            status=status,
            current_step="init",
            progress=0,
            params={"feature": feature},
            result={},
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_submit_uses_backlog_limit_and_start_uses_running_limit(self):
        for _ in range(2):
            self._add_task("processing")
        for _ in range(7):
            self._add_task("pending")

        start = runtime_guard.check_publication_task_admission(
            "report_pdf_audio_workflow",
            mode="start",
        )
        submit = runtime_guard.check_publication_task_admission(
            "report_pdf_audio_workflow",
            mode="submit",
        )

        self.assertFalse(start["allowed"])
        self.assertEqual(start["reason"], "feature_capacity")
        self.assertEqual(start["limit"], 2)
        self.assertTrue(submit["allowed"])
        self.assertEqual(submit["limit"], 10)

        self._add_task("pending")
        blocked_submit = runtime_guard.check_publication_task_admission(
            "report_pdf_audio_workflow",
            mode="submit",
        )

        self.assertFalse(blocked_submit["allowed"])
        self.assertEqual(blocked_submit["reason"], "feature_backlog")
        self.assertEqual(blocked_submit["limit"], 10)

    def test_backlog_limit_can_be_overridden_separately_from_running_limit(self):
        with patch.dict(
            os.environ,
            {
                "TASK_ADMISSION_FEATURE_LIMITS": "report_pdf_audio_workflow=2",
                "TASK_ADMISSION_FEATURE_BACKLOG_LIMITS": "report_pdf_audio_workflow=6",
            },
        ):
            self.assertEqual(runtime_guard._feature_limit("report_pdf_audio_workflow"), 2)
            self.assertEqual(runtime_guard._feature_backlog_limit("report_pdf_audio_workflow"), 6)

    def test_admission_retry_budget_does_not_fall_back_to_celery_default_three(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TASK_ADMISSION_MAX_RETRIES", None)
            self.assertEqual(runtime_guard._admission_retry_max_retries(), 100000)


if __name__ == "__main__":
    unittest.main()
