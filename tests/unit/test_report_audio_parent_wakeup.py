import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import report_audio_parent_wakeup as wakeup_service  # noqa: E402


class ReportAudioParentWakeupTest(unittest.TestCase):
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

    def _tasks(self, *, child_status="completed", parent_status="processing"):
        child = PublicationTask(
            feature="publisher",
            status=child_status,
            current_step="done" if child_status == "completed" else "render_failed",
            progress=100 if child_status == "completed" else 72,
            params={"feature": "publisher"},
            result={},
        )
        db.session.add(child)
        db.session.flush()
        parent = PublicationTask(
            celery_task_id="old-delayed-delivery",
            feature="report_pdf_audio_workflow",
            status=parent_status,
            current_step="pub_waiting",
            progress=95,
            params={"feature": "report_pdf_audio_workflow"},
            result={
                "waiting_child": {
                    "label": "publisher_video",
                    "child_id": str(child.id),
                    "status": "processing",
                    "step": "synthesis",
                    "updated_at": "2026-08-10T01:02:03Z",
                }
            },
        )
        db.session.add(parent)
        db.session.commit()
        return parent, child

    def test_completed_child_wakes_parent_exactly_once(self):
        parent, child = self._tasks()
        apply_result = {
            "async_result": SimpleNamespace(id="resume-delivery"),
            "admission": {"allowed": True},
            "deferred": False,
        }

        with patch.object(wakeup_service, "uuid4", return_value="resume-delivery"), patch.object(
            wakeup_service,
            "maybe_guarded_apply_async",
            return_value=apply_result,
        ) as mocked_apply:
            first = wakeup_service.wake_report_audio_parent_for_child(child.id)
            second = wakeup_service.wake_report_audio_parent_for_child(child.id)

        self.assertTrue(first["woken"], first)
        self.assertEqual(first["parent_task_id"], str(parent.id))
        self.assertEqual(first["celery_task_id"], "resume-delivery")
        self.assertFalse(second["woken"])
        self.assertEqual(second["reason"], "already_woken")
        mocked_apply.assert_called_once()
        call = mocked_apply.call_args.kwargs
        self.assertEqual(call["task_id"], str(parent.id))
        self.assertEqual(call["broker_task_id"], "resume-delivery")
        self.assertEqual(call["queue"], "report_audio_workflow")

        refreshed = db.session.get(PublicationTask, parent.id)
        self.assertEqual(refreshed.celery_task_id, "resume-delivery")
        self.assertEqual(refreshed.current_step, "pub_resume_queued")
        self.assertEqual(refreshed.waiting_child_status, "completed")
        self.assertEqual(
            refreshed.result["waiting_child"]["updated_at"],
            "publisher-terminal:resume-delivery",
        )
        self.assertEqual(refreshed.result["waiting_child"]["status"], "completed")

    def test_failed_child_also_wakes_parent(self):
        parent, child = self._tasks(child_status="failed")
        with patch.object(wakeup_service, "uuid4", return_value="failure-resume"), patch.object(
            wakeup_service,
            "maybe_guarded_apply_async",
            return_value={
                "async_result": SimpleNamespace(id="failure-resume"),
                "admission": {"allowed": True},
                "deferred": False,
            },
        ) as mocked_apply:
            result = wakeup_service.wake_report_audio_parent_for_child(child.id)

        self.assertTrue(result["woken"], result)
        mocked_apply.assert_called_once()
        refreshed = db.session.get(PublicationTask, parent.id)
        self.assertEqual(refreshed.waiting_child_status, "failed")
        self.assertEqual(refreshed.waiting_child_step, "render_failed")

    def test_non_terminal_child_is_ignored(self):
        _, child = self._tasks(child_status="processing")
        with patch.object(wakeup_service, "maybe_guarded_apply_async") as mocked_apply:
            result = wakeup_service.wake_report_audio_parent_for_child(child.id)

        self.assertFalse(result["woken"])
        self.assertEqual(result["reason"], "child_not_terminal")
        mocked_apply.assert_not_called()

    def test_unrelated_child_is_ignored(self):
        self._tasks()
        unrelated = PublicationTask(
            feature="publisher",
            status="completed",
            current_step="done",
            progress=100,
            params={"feature": "publisher"},
            result={},
        )
        db.session.add(unrelated)
        db.session.commit()

        with patch.object(wakeup_service, "maybe_guarded_apply_async") as mocked_apply:
            result = wakeup_service.wake_report_audio_parent_for_child(unrelated.id)

        self.assertFalse(result["woken"])
        self.assertEqual(result["reason"], "no_waiting_parent")
        mocked_apply.assert_not_called()

    def test_terminal_parent_is_ignored(self):
        for parent_status in ("completed", "canceled"):
            with self.subTest(parent_status=parent_status):
                parent, child = self._tasks(parent_status=parent_status)
                with patch.object(wakeup_service, "maybe_guarded_apply_async") as mocked_apply:
                    result = wakeup_service.wake_report_audio_parent_for_child(child.id)

                self.assertFalse(result["woken"])
                self.assertEqual(result["reason"], "no_waiting_parent")
                mocked_apply.assert_not_called()
                db.session.delete(parent)
                db.session.delete(child)
                db.session.commit()

    def test_publish_failure_restores_delayed_delivery_claim(self):
        parent, child = self._tasks()
        with patch.object(
            wakeup_service,
            "maybe_guarded_apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                wakeup_service.wake_report_audio_parent_for_child(child.id)

        refreshed = db.session.get(PublicationTask, parent.id)
        self.assertEqual(refreshed.celery_task_id, "old-delayed-delivery")
        self.assertEqual(refreshed.current_step, "pub_waiting")
        self.assertEqual(refreshed.waiting_child_status, "processing")
        self.assertEqual(refreshed.waiting_child_step, "synthesis")
        self.assertEqual(refreshed.waiting_child_updated_at, "2026-08-10T01:02:03Z")
        self.assertEqual(
            refreshed.result["waiting_child"]["updated_at"],
            "2026-08-10T01:02:03Z",
        )


if __name__ == "__main__":
    unittest.main()
