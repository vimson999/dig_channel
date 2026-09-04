import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event
from sqlalchemy.orm import Session


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services import report_audio_parent_wakeup as wakeup_service  # noqa: E402


class ReportAudioParentWakeupTest(unittest.TestCase):
    def test_candidate_search_never_loads_payload_and_broker_has_no_transaction(self):
        parent, child = self._tasks()
        parent_id, child_id = str(parent.id), str(child.id)
        db.session.remove()
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.lower().split()))

        def publish(*args, **kwargs):
            self.assertFalse(db.session().in_transaction())
            return {"async_result": SimpleNamespace(id=kwargs["broker_task_id"]), "deferred": False}

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(wakeup_service, "maybe_guarded_apply_async", side_effect=publish):
                first = wakeup_service.wake_report_audio_parent_for_child(child_id)
                split = len(statements)
                second = wakeup_service.wake_report_audio_parent_for_child(child_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertTrue(first["woken"])
        self.assertEqual(second["reason"], "already_woken")
        selects = [sql for sql in statements if sql.startswith("select")]
        payload_selects = [sql for sql in selects if "publication_tasks.result" in sql]
        self.assertEqual(len(payload_selects), 1, payload_selects)
        self.assertIn("where publication_tasks.id =", payload_selects[0])
        self.assertFalse(any("publication_tasks.result" in sql for sql in statements[split:]))
        saved = db.session.get(PublicationTask, parent_id)
        self.assertEqual(saved.result["waiting_child"]["status"], "completed")

    def test_unmatched_parent_search_does_not_read_json(self):
        _, child = self._tasks(parent_status="completed")
        child_id = str(child.id)
        db.session.remove()
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            result = wakeup_service.wake_report_audio_parent_for_child(child_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertEqual(result["reason"], "no_waiting_parent")
        self.assertFalse(any("publication_tasks.result" in sql for sql in statements))

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

    def test_failure_restore_preserves_audit_written_during_broker_io(self):
        parent, child = self._tasks()
        parent_id, child_id = parent.id, child.id
        db.session.remove()

        def fail_publish(*args, **kwargs):
            self.assertFalse(db.session().in_transaction())
            with Session(db.engine) as concurrent:
                row = concurrent.get(PublicationTask, parent_id)
                row.result = {
                    **row.result,
                    "concurrent_audit": {"delivered": True},
                    "waiting_child": {**row.result["waiting_child"], "audit_note": "keep"},
                }
                concurrent.commit()
            raise RuntimeError("broker unavailable")

        with patch.object(wakeup_service, "maybe_guarded_apply_async", side_effect=fail_publish):
            with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                wakeup_service.wake_report_audio_parent_for_child(child_id)

        saved = db.session.get(PublicationTask, parent_id)
        self.assertEqual(saved.result["concurrent_audit"], {"delivered": True})
        self.assertEqual(saved.result["waiting_child"]["audit_note"], "keep")
        self.assertEqual(saved.celery_task_id, "old-delayed-delivery")
        self.assertEqual(saved.result["waiting_child"]["status"], "processing")

    def test_failure_restore_never_reverts_a_terminal_or_advanced_parent(self):
        for status, step in (("canceled", "pub_resume_queued"), ("processing", "finalizing")):
            with self.subTest(status=status, step=step):
                parent, child = self._tasks()
                parent_id, child_id = parent.id, child.id
                db.session.remove()

                def fail_publish(*args, **kwargs):
                    with Session(db.engine) as concurrent:
                        row = concurrent.get(PublicationTask, parent_id)
                        row.status = status
                        row.current_step = step
                        row.error_message = "new state must survive"
                        concurrent.commit()
                    raise RuntimeError("broker unavailable")

                with patch.object(wakeup_service, "maybe_guarded_apply_async", side_effect=fail_publish):
                    with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                        wakeup_service.wake_report_audio_parent_for_child(child_id)
                saved = db.session.get(PublicationTask, parent_id)
                self.assertEqual((saved.status, saved.current_step), (status, step))
                self.assertEqual(saved.error_message, "new state must survive")

    def test_admission_deferral_then_broker_failure_restores_delayed_fallback(self):
        from services import runtime_guard
        from tasks.report_audio_workflow import process_report_audio_workflow_job

        for reason in ("db_idle_in_transaction_pressure", "feature_backlog", "feature_capacity"):
            with self.subTest(reason=reason):
                parent, child = self._tasks()
                parent_id, child_id = parent.id, child.id
                db.session.remove()

                def fail_publish(**kwargs):
                    self.assertFalse(db.session().in_transaction())
                    raise RuntimeError("broker unavailable after defer")

                with patch.object(runtime_guard, "check_publication_task_admission", return_value={
                    "allowed": False, "reason": reason, "message": "wait", "retry_after": 30,
                }), patch.object(process_report_audio_workflow_job, "apply_async", side_effect=fail_publish):
                    with self.assertRaisesRegex(RuntimeError, "broker unavailable after defer"):
                        wakeup_service.wake_report_audio_parent_for_child(child_id)

                saved = db.session.get(PublicationTask, parent_id)
                self.assertEqual(saved.celery_task_id, "old-delayed-delivery")
                self.assertEqual(saved.current_step, "pub_waiting")
                self.assertEqual(saved.waiting_child_updated_at, "2026-08-10T01:02:03Z")
                self.assertEqual(saved.result["runtime_guard"]["reason"], reason)
                self.assertEqual(len(saved.result["runtime_guard_history"]), 1)


if __name__ == "__main__":
    unittest.main()
