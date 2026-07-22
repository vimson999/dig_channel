import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.content_pipeline import audio_clean_task_service, auto_publish_handoff, pdf_clean_task_service  # noqa: E402
from services.zsxq_workflow_service import zsxq_workflow_service  # noqa: E402


class ContentPipelineSubmissionQueryDisciplineTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
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
        self.temp_dir.cleanup()

    def _capture_submission(self, module, submit, **kwargs):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        guarded_result = {
            "async_result": SimpleNamespace(id="celery-1"),
            "admission": {"allowed": True, "reason": "ok"},
            "deferred": False,
            "deferred_state": None,
        }
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(module, "maybe_guarded_apply_async", return_value=guarded_result) as guarded:
                payload = submit(**kwargs)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        return payload, statements, guarded

    def test_pdf_submit_never_refreshes_wide_task_after_commit(self):
        path = Path(self.temp_dir.name) / "report.pdf"
        path.write_bytes(b"%PDF-1.4\nfixture")

        payload, statements, guarded = self._capture_submission(
            pdf_clean_task_service,
            pdf_clean_task_service.submit_pdf_clean_task,
            title="PDF 清洗",
            files=[str(path)],
            metadata={"sync_run_id": "run-1"},
        )

        self.assertEqual(payload["celery_task_id"], "celery-1")
        self.assertEqual(payload["params"]["feature"], "content_pdf_clean")
        self.assertEqual(payload["result"]["files"][0]["status"], "waiting")
        self.assertFalse(any(
            sql.startswith("select") and " from publication_tasks " in sql
            for sql in statements
        ), statements)
        self.assertEqual(guarded.call_args.kwargs["task_id"], payload["id"])
        self.assertNotIn("task", guarded.call_args.kwargs)

        persisted = db.session.get(PublicationTask, payload["id"])
        self.assertEqual(persisted.celery_task_id, "celery-1")

    def test_audio_submit_never_refreshes_wide_task_after_commit(self):
        path = Path(self.temp_dir.name) / "recording.mp3"
        path.write_bytes(b"ID3fixture")

        payload, statements, guarded = self._capture_submission(
            audio_clean_task_service,
            audio_clean_task_service.submit_audio_clean_task,
            title="音频清洗",
            files=[str(path)],
            metadata={"sync_run_id": "run-1"},
        )

        self.assertEqual(payload["celery_task_id"], "celery-1")
        self.assertEqual(payload["params"]["feature"], "content_audio_clean")
        self.assertEqual(payload["result"]["files"][0]["status"], "waiting")
        self.assertFalse(any(
            sql.startswith("select") and " from publication_tasks " in sql
            for sql in statements
        ), statements)
        self.assertEqual(guarded.call_args.kwargs["task_id"], payload["id"])
        self.assertNotIn("task", guarded.call_args.kwargs)

    def test_auto_publish_submit_never_refreshes_wide_task_after_commit(self):
        output_dir = Path(self.temp_dir.name) / "clean-output"
        output_dir.mkdir()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        def dispatch(_platform, _task_id, *, broker_task_id, retry):
            return {
                "celery_id": broker_task_id,
                "admission": {"allowed": True, "reason": "ok"},
                "deferred": False,
                "deferred_state": None,
            }

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(
                auto_publish_handoff,
                "dispatch_platform_publish",
                side_effect=dispatch,
            ) as dispatch_mock:
                payload = auto_publish_handoff.submit_publish_clean_output_task(
                    title="清洗产物发布",
                    file_type="pdf",
                    output_dir=str(output_dir),
                    options={"enabled": True},
                    source_task_id="clean-task-1",
                    user_id="user-1",
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertFalse(any(
            sql.startswith("select") and " from publication_tasks " in sql
            for sql in statements
        ), statements)
        self.assertEqual(payload["result"]["queued"], True)
        self.assertEqual(payload["celery_task_id"], dispatch_mock.call_args.kwargs["broker_task_id"])
        persisted = db.session.get(PublicationTask, payload["id"])
        self.assertEqual(persisted.celery_task_id, payload["celery_task_id"])
        self.assertEqual(persisted.result["queued"], True)

    def test_auto_publish_queue_failure_uses_narrow_write_without_payload_reload(self):
        output_dir = Path(self.temp_dir.name) / "failed-clean-output"
        output_dir.mkdir()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(
                auto_publish_handoff,
                "dispatch_platform_publish",
                side_effect=RuntimeError("broker unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "入队失败"):
                    auto_publish_handoff.submit_publish_clean_output_task(
                        title="清洗产物发布",
                        file_type="audio",
                        output_dir=str(output_dir),
                        options={"enabled": True},
                    )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertFalse(any(
            sql.startswith("select") and " from publication_tasks " in sql
            for sql in statements
        ), statements)
        persisted = PublicationTask.query.one()
        self.assertEqual(persisted.status, "failed")
        self.assertEqual(persisted.current_step, "failed")
        self.assertEqual(persisted.result["reason"], "queue_failed")

    def test_clean_refresh_reads_projection_without_result_payload(self):
        completed = PublicationTask(
            id="clean-pdf-1",
            status="completed",
            current_step="done",
            progress=100,
            feature="content_pdf_clean",
            params={"feature": "content_pdf_clean", "large": "x" * 1000},
            result={"local_output_dir": "/tmp/clean-pdf-1", "files_count": 3},
        )
        failed = PublicationTask(
            id="clean-audio-1",
            status="failed",
            current_step="failed",
            progress=20,
            feature="content_audio_clean",
            params={"feature": "content_audio_clean", "large": "x" * 1000},
            result={"large": "x" * 1000},
        )
        db.session.add_all([completed, failed])
        db.session.commit()
        workflow = SimpleNamespace(
            id="workflow-1",
            clean_task_ids=[completed.id, failed.id],
            stage_results={
                "clean": {
                    "status": "running",
                    "tasks": [{
                        "task_id": completed.id,
                        "source_refs": [{"attachment_id": "attachment-1"}],
                    }],
                }
            },
        )
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(zsxq_workflow_service, "_queue_notification"):
                zsxq_workflow_service._refresh_clean_stage(workflow)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        task_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from publication_tasks " in sql
        ]
        self.assertEqual(len(task_selects), 1, statements)
        self.assertNotIn("publication_tasks.params", " ".join(task_selects))
        self.assertNotIn("publication_tasks.result", task_selects[0])
        self.assertIn("publication_tasks.content_clean_output_dir", task_selects[0])
        self.assertIn("publication_tasks.content_clean_files_count", task_selects[0])
        self.assertEqual(workflow.stage_results["clean"]["status"], "partial")
        summary = {
            item["task_id"]: item
            for item in workflow.stage_results["clean"]["tasks"]
        }
        self.assertEqual(summary["clean-pdf-1"]["local_output_dir"], "/tmp/clean-pdf-1")
        self.assertEqual(
            summary["clean-pdf-1"]["source_refs"],
            [{"attachment_id": "attachment-1"}],
        )
        self.assertEqual(summary["clean-audio-1"]["file_type"], "audio")

    def test_publish_submission_reuses_clean_summary_without_task_query(self):
        workflow = SimpleNamespace(
            id="workflow-1",
            batch_id="batch-1",
            sync_run_id="sync-run-1",
            clean_task_ids=["clean-pdf-1"],
            publish_task_ids=[],
            config_snapshot={
                "stages": {"publish": True},
                "publish": {"enabled": True},
            },
            stage_results={
                "clean": {
                    "status": "completed",
                    "tasks": [{
                        "task_id": "clean-pdf-1",
                        "status": "completed",
                        "file_type": "pdf",
                        "local_output_dir": "/tmp/clean-pdf-1",
                        "source_refs": [{
                            "attachment_id": "attachment-1",
                            "output_paths": ["/tmp/clean-pdf-1/report.pdf"],
                        }],
                    }],
                }
            },
            current_stage="clean",
        )
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch(
                "services.zsxq_workflow_service.submit_publish_clean_output_task",
                return_value={"id": "publish-1", "celery_task_id": "publish-celery-1"},
            ) as submit_publish, patch.object(zsxq_workflow_service, "_queue_notification"):
                zsxq_workflow_service._submit_publish_stage(workflow)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertFalse(any(" from publication_tasks " in sql for sql in statements), statements)
        self.assertEqual(workflow.publish_task_ids, ["publish-1"])
        submit_publish.assert_called_once()
        self.assertEqual(submit_publish.call_args.kwargs["source_task_id"], "clean-pdf-1")
        self.assertEqual(
            submit_publish.call_args.kwargs["metadata"]["source_refs"][0]["attachment_id"],
            "attachment-1",
        )

    def test_clean_stage_event_persists_source_refs_before_refresh(self):
        workflow = SimpleNamespace(
            stage_results={"clean": {"status": "running", "tasks": []}},
        )

        zsxq_workflow_service._merge_stage_event(workflow, {
            "stage": "clean",
            "task_id": "clean-1",
            "source_refs": [{"attachment_id": "attachment-1"}],
        })

        self.assertEqual(
            workflow.stage_results["clean"]["tasks"],
            [{
                "task_id": "clean-1",
                "source_refs": [{"attachment_id": "attachment-1"}],
            }],
        )

    def test_publish_refresh_polling_reads_state_without_params_or_result(self):
        task = PublicationTask(
            id="publish-task-1",
            status="processing",
            current_step="publishing",
            progress=60,
            feature="zsxq_clean_output_publish",
            params={"feature": "zsxq_clean_output_publish", "large": "x" * 1000},
            result={"large": "x" * 1000},
        )
        db.session.add(task)
        db.session.commit()
        workflow = SimpleNamespace(
            id="workflow-1",
            publish_task_ids=[task.id],
            stage_results={
                "publish": {
                    "status": "running",
                    "tasks": [{"task_id": task.id, "file_type": "pdf"}],
                }
            },
        )
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch(
                "services.zsxq_workflow_service.summarize_publish_runs_for_request",
                return_value={
                    "summary": {
                        "submitted_count": 2,
                        "verified_count": 1,
                        "failed_count": 0,
                        "pending_verify_count": 1,
                    }
                },
            ), patch.object(zsxq_workflow_service, "_queue_notification"):
                zsxq_workflow_service._refresh_publish_stage(workflow)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        task_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from publication_tasks " in sql
        ]
        self.assertEqual(len(task_selects), 1, statements)
        self.assertNotIn("publication_tasks.params", task_selects[0])
        self.assertNotIn("publication_tasks.result", task_selects[0])
        self.assertEqual(workflow.stage_results["publish"]["status"], "running")
        self.assertEqual(workflow.stage_results["publish"]["tasks"][0]["file_type"], "pdf")

    def test_publish_refresh_terminal_runtime_avoids_result_fallback(self):
        task = PublicationTask(
            id="publish-task-2",
            status="completed",
            current_step="done",
            progress=100,
            feature="zsxq_clean_output_publish",
            params={"feature": "zsxq_clean_output_publish", "large": "x" * 1000},
            result={"large": "x" * 1000},
        )
        db.session.add(task)
        db.session.commit()
        workflow = SimpleNamespace(
            id="workflow-2",
            publish_task_ids=[task.id],
            stage_results={"publish": {"status": "running", "tasks": []}},
        )
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch(
                "services.zsxq_workflow_service.summarize_publish_runs_for_request",
                return_value={
                    "summary": {
                        "submitted_count": 2,
                        "verified_count": 2,
                        "failed_count": 0,
                        "pending_verify_count": 0,
                    }
                },
            ), patch.object(zsxq_workflow_service, "_queue_notification"):
                zsxq_workflow_service._refresh_publish_stage(workflow)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        task_selects = [
            sql for sql in statements
            if sql.startswith("select") and " from publication_tasks " in sql
        ]
        self.assertEqual(len(task_selects), 1, statements)
        self.assertNotIn("publication_tasks.params", task_selects[0])
        self.assertNotIn("publication_tasks.result", task_selects[0])
        self.assertEqual(workflow.stage_results["publish"]["status"], "completed")
        self.assertEqual(workflow.stage_results["publish"]["tasks"][0]["verified_count"], 2)


if __name__ == "__main__":
    unittest.main()
