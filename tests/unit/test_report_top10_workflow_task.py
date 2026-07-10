import sys
import unittest
from pathlib import Path
from flask import Flask
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from tasks import report_top10_workflow  # noqa: E402


class ReportTop10WorkflowTaskTest(unittest.TestCase):
    def test_single_progress_uses_dynamic_total(self):
        self.assertEqual(report_top10_workflow._single_progress(0, 4), 5)
        self.assertEqual(report_top10_workflow._single_progress(2, 4), 37)
        self.assertEqual(report_top10_workflow._single_progress(4, 4), 70)

    def test_mark_failed_safely_rolls_back_before_loading_task(self):
        events = []

        class FakeSession:
            def rollback(self):
                events.append("rollback")

            def remove(self):
                events.append("remove")

        fake_task = type("Task", (), {"status": "processing"})()
        fake_db = type("Db", (), {"session": FakeSession()})()

        def fake_load_task(task_id, *columns):
            events.append("load")
            return fake_task

        def fake_mark_failed(task, message):
            events.append("mark_failed")

        with (
            mock.patch.object(report_top10_workflow, "db", fake_db),
            mock.patch.object(report_top10_workflow, "_load_task", side_effect=fake_load_task),
            mock.patch.object(report_top10_workflow, "_mark_failed", side_effect=fake_mark_failed),
        ):
            report_top10_workflow._mark_failed_safely("task-1", "boom")

        self.assertEqual(events[:2], ["rollback", "load"])
        self.assertIn("mark_failed", events)

    def test_mark_failed_safely_removes_bad_session_when_rollback_fails(self):
        events = []

        class FakeSession:
            def rollback(self):
                events.append("rollback")
                raise RuntimeError("bad transaction")

            def remove(self):
                events.append("remove")

        fake_task = type("Task", (), {"status": "processing"})()
        fake_db = type("Db", (), {"session": FakeSession()})()

        with (
            mock.patch.object(report_top10_workflow, "db", fake_db),
            mock.patch.object(report_top10_workflow, "_load_task", return_value=fake_task),
            mock.patch.object(report_top10_workflow, "_mark_failed") as mark_failed,
        ):
            report_top10_workflow._mark_failed_safely("task-1", "boom")

        self.assertEqual(events[:2], ["rollback", "remove"])
        mark_failed.assert_called_once_with(fake_task, "boom")

    def test_wait_child_reloads_terminal_child_after_parent_summary_commit(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)

        with app.app_context():
            db.metadata.create_all(bind=db.engine, tables=[PublicationTask.__table__])
            parent = PublicationTask(
                status="processing",
                current_step="single_gemini_waiting",
                progress=40,
                params={"feature": "report_top10_workflow"},
                result={},
            )
            child = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={"feature": "report_pdf_gemini"},
                result={"final_response_text": "Top10 Gemini 完整返回" * 20},
                completed_at=report_top10_workflow.datetime.utcnow(),
            )
            db.session.add_all([parent, child])
            db.session.commit()

            returned = report_top10_workflow._wait_child(
                parent.id,
                child.id,
                "single_gemini_01",
                start_progress=5,
                end_progress=20,
            )

            self.assertEqual(returned.id, child.id)
            self.assertEqual(returned.status, "completed")
            self.assertEqual(returned.current_step, "done")
            self.assertIn("Top10", returned.result["final_response_text"])

            db.session.expire_all()
            refreshed_parent = PublicationTask.query.filter(PublicationTask.id == parent.id).one()
            self.assertEqual(refreshed_parent.result["children"]["single_gemini_01"]["job_id"], child.id)
            self.assertEqual(refreshed_parent.result["children"]["single_gemini_01"]["status"], "completed")

            db.session.remove()
            db.metadata.drop_all(bind=db.engine, tables=[PublicationTask.__table__])

    def test_reusable_single_report_reads_script_material_workflow_task(self):
        app = Flask(__name__)
        app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)

        with app.app_context():
            db.metadata.create_all(bind=db.engine, tables=[PublicationTask.__table__])
            structured = {
                "title": "AI 算力深度报告",
                "final_script_for_tts": "这是一段可用于 Top10 编排的单篇研报文案。",
            }
            task = PublicationTask(
                status="completed",
                current_step="done",
                progress=100,
                params={
                    "feature": "report_script_material_workflow",
                    "source_feature": "zsxq_attachment",
                    "source_task_id": "asset-1",
                    "report_script_material_workflow": {
                        "pdf": {"attachment_id": "asset-1", "filename": "report.pdf"},
                    },
                },
                result={"structured_result": structured},
            )
            db.session.add(task)
            db.session.commit()

            report = report_top10_workflow._reusable_single_report_for_pdf({
                "attachment_id": "asset-1",
                "filename": "report.pdf",
            })

            self.assertIsNotNone(report)
            self.assertEqual(report["source"], "history_report_script_material_workflow")
            self.assertEqual(report["source_task_id"], task.id)
            self.assertEqual(report["single_report_script"], structured["final_script_for_tts"])

            db.session.remove()
            db.metadata.drop_all(bind=db.engine, tables=[PublicationTask.__table__])


if __name__ == "__main__":
    unittest.main()
