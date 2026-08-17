import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks.zsxq_browser_import import run_browser_import  # noqa: E402
from models import db  # noqa: E402
from models.zsxq_sync import ZsxqBrowserSubmission  # noqa: E402


class ZsxqBrowserImportTaskTest(unittest.TestCase):
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

    def _submission(self, *, status="queued", result=None):
        row = ZsxqBrowserSubmission(
            idempotency_key="zsxq-sidebar:mp3:sidebar-mp3-task-test",
            run_id="sidebar-mp3-task-test",
            kind="mp3",
            payload_sha256="a" * 64,
            manifest_path="/Users/v9/Downloads/inbox/result.json",
            celery_task_id="celery-stable-id",
            status=status,
            attempt=0,
            result=result or {"preview": {"summary": {"files": 1}}},
        )
        db.session.add(row)
        db.session.commit()
        return row

    def test_task_imports_without_dispatching_clean_by_default(self):
        import_result = {
            "manifests": [
                {"run_id": "run-pdf", "kind": "pdf", "created_attachments": 1}
            ],
            "summary": {"created_attachments": 1},
        }
        with patch("app.create_app", return_value=self.app), patch(
            "tasks.zsxq_browser_import.zsxq_browser_import_service.import_manifests",
            return_value=import_result,
        ) as importer, patch(
            "tasks.zsxq_browser_import.zsxq_workflow_service.start_browser_import_workflow"
        ) as start_workflow:
            result = run_browser_import.run(
                ["/Users/v9/Downloads/inbox/result.json"],
                dispatch_clean=False,
            )

        self.assertEqual(result, import_result)
        importer.assert_called_once_with(
            ["/Users/v9/Downloads/inbox/result.json"],
            dispatch_clean=False,
        )
        start_workflow.assert_not_called()

    def test_task_starts_existing_workflow_for_each_media_type(self):
        import_result = {
            "manifests": [
                {"run_id": "run-pdf", "kind": "pdf", "created_attachments": 1},
                {"run_id": "run-mp3", "kind": "mp3", "created_attachments": 1},
            ],
            "summary": {"created_attachments": 2},
        }
        with patch("app.create_app", return_value=self.app), patch(
            "tasks.zsxq_browser_import.zsxq_browser_import_service.import_manifests",
            return_value=import_result,
        ), patch(
            "tasks.zsxq_browser_import.zsxq_workflow_service.start_browser_import_workflow",
            side_effect=[
                {"workflow_run_id": "workflow-pdf", "clean_task_ids": ["pdf-clean"]},
                {"workflow_run_id": "workflow-mp3", "clean_task_ids": ["audio-clean"]},
            ],
        ) as start_workflow:
            result = run_browser_import.run(
                [
                    "/Users/v9/Downloads/inbox/pdf.json",
                    "/Users/v9/Downloads/inbox/mp3.json",
                ],
                dispatch_clean=True,
                cleaner={"pdf": {"profile": "default"}},
                auto_publish={"enabled": False},
            )

        self.assertEqual(
            result["clean_handoffs"]["run-pdf"]["workflow_run_id"], "workflow-pdf"
        )
        self.assertEqual(
            result["clean_handoffs"]["run-mp3"]["workflow_run_id"], "workflow-mp3"
        )
        self.assertEqual(start_workflow.call_count, 2)
        self.assertEqual(start_workflow.call_args_list[0].kwargs["run_id"], "run-pdf")
        self.assertEqual(start_workflow.call_args_list[0].kwargs["kind"], "pdf")
        self.assertEqual(start_workflow.call_args_list[1].kwargs["run_id"], "run-mp3")
        self.assertEqual(start_workflow.call_args_list[1].kwargs["kind"], "mp3")
        self.assertEqual(
            start_workflow.call_args_list[0].kwargs["auto_publish"], {"enabled": False}
        )

    def test_task_starts_sync_workflow_for_zero_file_manifest(self):
        import_result = {
            "manifests": [
                {
                    "run_id": "run-mp3-zero",
                    "kind": "mp3",
                    "files": 0,
                    "created_attachments": 0,
                    "tab_id": "88844545452542",
                    "tab_label": "调研纪要",
                }
            ],
            "summary": {"created_attachments": 0},
        }
        workflow_result = {
            "workflow_run_id": "workflow-mp3-zero",
            "status": "completed",
            "clean_task_ids": [],
            "publish_task_ids": [],
        }
        with patch("app.create_app", return_value=self.app), patch(
            "tasks.zsxq_browser_import.zsxq_browser_import_service.import_manifests",
            return_value=import_result,
        ), patch(
            "tasks.zsxq_browser_import.zsxq_workflow_service.start_browser_import_workflow",
            return_value=workflow_result,
        ) as start_workflow:
            result = run_browser_import.run(
                ["/Users/v9/Downloads/inbox/empty-mp3.json"],
                dispatch_clean=True,
                auto_publish={"enabled": True},
            )

        self.assertEqual(
            result["clean_handoffs"]["run-mp3-zero"]["workflow_run_id"],
            "workflow-mp3-zero",
        )
        start_workflow.assert_called_once()
        self.assertEqual(start_workflow.call_args.kwargs["kind"], "mp3")
        self.assertEqual(start_workflow.call_args.kwargs["manifest"]["files"], 0)

    def test_submission_worker_claims_imports_and_completes_once(self):
        submission = self._submission()
        import_result = {
            "manifests": [
                {"run_id": "sync-run-1", "kind": "mp3", "created_attachments": 1}
            ],
            "summary": {"created_attachments": 1},
        }
        workflow_result = {
            "workflow_run_id": "workflow-1",
            "clean_task_ids": ["clean-1"],
        }
        with patch("app.create_app", return_value=self.app), patch(
            "tasks.zsxq_browser_import.zsxq_browser_import_service.import_manifests",
            return_value=import_result,
        ) as importer, patch(
            "tasks.zsxq_browser_import.zsxq_workflow_service.start_browser_import_workflow",
            return_value=workflow_result,
        ) as start_workflow:
            result = run_browser_import.run(
                [submission.manifest_path],
                dispatch_clean=True,
                auto_publish={"enabled": True},
                submission_id=submission.id,
            )

        db.session.expire_all()
        stored = ZsxqBrowserSubmission.query.get(submission.id)
        self.assertEqual(stored.status, "completed")
        self.assertEqual(
            stored.result["worker_result"]["clean_handoffs"]["sync-run-1"],
            workflow_result,
        )
        self.assertEqual(result["clean_handoffs"]["sync-run-1"], workflow_result)
        importer.assert_called_once()
        start_workflow.assert_called_once()

    def test_duplicate_submission_message_returns_receipt_without_side_effects(self):
        prior_result = {
            "manifests": [{"run_id": "sync-run-1", "kind": "mp3"}],
            "summary": {"created_attachments": 1},
            "clean_handoffs": {"sync-run-1": {"workflow_run_id": "workflow-1"}},
        }
        submission = self._submission(
            status="completed", result={"worker_result": prior_result}
        )
        with patch("app.create_app", return_value=self.app), patch(
            "tasks.zsxq_browser_import.zsxq_browser_import_service.import_manifests"
        ) as importer, patch(
            "tasks.zsxq_browser_import.zsxq_workflow_service.start_browser_import_workflow"
        ) as start_workflow:
            result = run_browser_import.run(
                [submission.manifest_path],
                dispatch_clean=True,
                submission_id=submission.id,
            )

        self.assertTrue(result["replayed"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], prior_result)
        importer.assert_not_called()
        start_workflow.assert_not_called()

    def test_submission_failure_is_persisted_for_safe_retry(self):
        submission = self._submission()
        with patch("app.create_app", return_value=self.app), patch(
            "tasks.zsxq_browser_import.zsxq_browser_import_service.import_manifests",
            side_effect=RuntimeError("import failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "import failed"):
                run_browser_import.run(
                    [submission.manifest_path],
                    submission_id=submission.id,
                )

        db.session.expire_all()
        stored = ZsxqBrowserSubmission.query.get(submission.id)
        self.assertEqual(stored.status, "failed")
        self.assertIn("import failed", stored.error_message)


if __name__ == "__main__":
    unittest.main()
