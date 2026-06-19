import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import report_video  # noqa: E402


class ReportVideoFailureGuardTest(unittest.TestCase):
    def test_celery_on_failure_marks_and_sends_direct_email(self):
        task = report_video.ReportVideoFailureTask()
        task.name = "tasks.report_video.generate_videos_task"
        details = {
            "entity_type": "task",
            "entity_id": "task-1",
            "parent_task_id": "task-1",
            "persisted": True,
        }

        with patch.object(report_video, "_mark_report_video_failure_safely", return_value=details) as mark_failed, \
             patch.object(report_video, "_send_direct_report_video_failure_email") as send_email:
            task.on_failure(RuntimeError("boom"), "celery-1", ("task-1",), {}, None)

        mark_failed.assert_called_once_with(
            "tasks.report_video.generate_videos_task",
            args=("task-1",),
            kwargs={},
            exc=ANY,
        )
        send_email.assert_called_once_with(
            celery_task_name="tasks.report_video.generate_videos_task",
            celery_task_id="celery-1",
            details=details,
            error="boom",
        )

    def test_mark_failure_returns_persist_error_when_app_context_unavailable(self):
        with patch("app.create_app", side_effect=RuntimeError("db unavailable")):
            details = report_video._mark_report_video_failure_safely(
                "tasks.report_video.generate_videos_task",
                args=("task-1",),
                kwargs={},
                exc=RuntimeError("boom"),
            )

        self.assertFalse(details["persisted"])
        self.assertIn("db unavailable", details["persist_error"])


if __name__ == "__main__":
    unittest.main()
