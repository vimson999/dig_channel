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
        PublicationTask.__table__.create(bind=db.engine)
        runtime_guard._DB_PRESSURE_CACHE["expires_at"] = 0.0
        runtime_guard._DB_PRESSURE_CACHE["snapshot"] = None

    def tearDown(self):
        db.session.remove()
        PublicationTask.__table__.drop(bind=db.engine)
        self.ctx.pop()

    def _add_task(self, status, feature="report_pdf_audio_workflow", step="init"):
        task = PublicationTask(
            status=status,
            current_step=step,
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

    def test_script_material_workflow_has_independent_default_capacity(self):
        self.assertEqual(runtime_guard._feature_limit("report_script_material_workflow"), 2)
        self.assertEqual(runtime_guard._feature_backlog_limit("report_script_material_workflow"), 50)

    def test_mp3_video_waiting_children_do_not_consume_start_capacity(self):
        passive_steps = (
            "landscape_video_waiting",
            "landscape_video_queued",
            "landscape_video_synthesis",
            "vertical_video_wait_existing",
            "vertical_video_done",
        )
        for step in passive_steps:
            self._add_task("processing", feature="mp3_video_workflow", step=step)

        start = runtime_guard.check_publication_task_admission(
            "mp3_video_workflow",
            mode="start",
        )

        self.assertTrue(start["allowed"])
        self.assertEqual(start["active_count"], 0)
        self.assertEqual(start["limit"], 1)

        self._add_task("processing", feature="mp3_video_workflow", step="generate_visual_assets")
        blocked = runtime_guard.check_publication_task_admission(
            "mp3_video_workflow",
            mode="start",
        )

        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "feature_capacity")
        self.assertEqual(blocked["active_count"], 1)

    def test_admission_retry_budget_does_not_fall_back_to_celery_default_three(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TASK_ADMISSION_MAX_RETRIES", None)
            self.assertEqual(runtime_guard._admission_retry_max_retries(), 100000)

    def test_deferred_task_keeps_guard_message_out_of_error_message(self):
        task = self._add_task("pending", feature="publisher")
        admission = {
            "allowed": False,
            "reason": "feature_capacity",
            "message": "系统保护：publisher 同类任务达到当前运行上限 4，稍后自动重试。",
            "feature": "publisher",
            "active_count": 4,
            "limit": 4,
        }

        saved = runtime_guard._mark_publication_task_deferred(
            task.id,
            admission=admission,
            queue="render",
            countdown=300,
        )

        self.assertTrue(saved)
        db.session.refresh(task)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.current_step, "deferred_capacity")
        self.assertIsNone(task.error_message)
        self.assertEqual(task.result["runtime_guard"]["message"], admission["message"])


if __name__ == "__main__":
    unittest.main()
