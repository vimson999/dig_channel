import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from services.zsxq_workflow_service import ZsxqWorkflowService  # noqa: E402


class ZsxqBrowserImportWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_browser_import_uses_existing_workflow_notification_nodes(self):
        service = ZsxqWorkflowService()
        captured = {}

        def create_workflow(**kwargs):
            workflow = kwargs["workflow_factory"]()
            workflow.id = "workflow-browser-1"
            captured["workflow"] = workflow
            return {"id": workflow.id}

        with patch.object(service, "ensure_schema"), patch.object(
            service,
            "_create_workflow_with_db_retry",
            side_effect=create_workflow,
        ), patch.object(
            service,
            "_load_workflow_run",
            side_effect=lambda workflow_id, include_runtime=True: captured["workflow"],
        ), patch.object(
            service, "_queue_notification"
        ) as notify, patch.object(
            service,
            "_advance_workflow_loaded",
            return_value={"id": "workflow-browser-1", "current_stage": "clean"},
        ) as advance, patch.object(
            db.session, "commit"
        ):
            result = service.start_browser_import_workflow(
                run_id="browser-run-1",
                kind="pdf",
                manifest={
                    "manifest_path": "/Users/v9/Downloads/inbox/pdf.json",
                    "tab_id": "51184248544214",
                    "tab_label": "外资研报",
                    "files": 2,
                    "created_topics": 2,
                    "created_attachments": 2,
                },
                cleaner={"engine": "local"},
                auto_publish={"enabled": True},
            )

        workflow = captured["workflow"]
        self.assertEqual(result["current_stage"], "clean")
        self.assertEqual(workflow.sync_run_id, "browser-run-1")
        self.assertEqual(workflow.stage_results["sync"]["status"], "completed")
        self.assertEqual(
            [call.args[1] for call in notify.call_args_list],
            ["workflow_started", "sync_completed"],
        )
        advance.assert_called_once_with(workflow, reason="browser_import_finished")
        self.assertTrue(captured["workflow"].config_snapshot["publish"]["enabled"])
        self.assertTrue(captured["workflow"].config_snapshot["clean"]["auto_pdf_clean"])
        self.assertFalse(
            captured["workflow"].config_snapshot["clean"]["auto_audio_clean"]
        )

    def test_browser_import_notifies_workflow_failure_when_next_stage_cannot_start(
        self,
    ):
        service = ZsxqWorkflowService()
        captured = {}

        def create_workflow(**kwargs):
            workflow = kwargs["workflow_factory"]()
            workflow.id = "workflow-browser-failure"
            captured["workflow"] = workflow
            return {"id": workflow.id}

        with patch.object(service, "ensure_schema"), patch.object(
            service,
            "_create_workflow_with_db_retry",
            side_effect=create_workflow,
        ), patch.object(
            service,
            "_load_workflow_run",
            side_effect=lambda workflow_id, include_runtime=True: captured["workflow"],
        ), patch.object(
            service, "_queue_notification"
        ) as notify, patch.object(
            service,
            "_advance_workflow_loaded",
            side_effect=RuntimeError("clean queue unavailable"),
        ), patch.object(
            service,
            "_commit_and_payload",
            return_value={"id": "workflow-browser-failure"},
        ), patch.object(
            db.session, "commit"
        ), patch.object(
            db.session, "rollback"
        ):
            with self.assertRaisesRegex(RuntimeError, "clean queue unavailable"):
                service.start_browser_import_workflow(
                    run_id="browser-run-failure",
                    kind="mp3",
                    manifest={"tab_id": "88844545452542", "tab_label": "调研纪要"},
                )

        self.assertIn(
            "workflow_failed",
            [call.args[1] for call in notify.call_args_list],
        )

    def test_zero_file_browser_import_completes_sync_workflow_without_child_tasks(self):
        service = ZsxqWorkflowService()
        captured = {}

        def create_workflow(**kwargs):
            workflow = kwargs["workflow_factory"]()
            workflow.id = "workflow-browser-zero"
            captured["workflow"] = workflow
            return {"id": workflow.id}

        with patch.object(service, "ensure_schema"), patch.object(
            service,
            "_create_workflow_with_db_retry",
            side_effect=create_workflow,
        ), patch.object(
            service,
            "_load_workflow_run",
            side_effect=lambda workflow_id, include_runtime=True: captured["workflow"],
        ), patch.object(
            service, "_queue_notification"
        ) as notify, patch.object(
            db.session, "commit"
        ):
            result = service.start_browser_import_workflow(
                run_id="browser-run-zero",
                kind="mp3",
                manifest={
                    "group_id": "28888112822211",
                    "tab_id": "88844545452542",
                    "tab_label": "调研纪要",
                    "files": 0,
                    "created_attachments": 0,
                    "reused_attachments": 0,
                },
                auto_publish={"enabled": True},
            )

        workflow = captured["workflow"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["current_stage"], "done")
        self.assertEqual(workflow.clean_task_ids, [])
        self.assertEqual(workflow.publish_task_ids, [])
        self.assertEqual(workflow.stage_results["clean"]["reason"], "no_new_files")
        self.assertEqual(workflow.stage_results["publish"]["reason"], "no_new_files")
        self.assertEqual(
            [call.args[1] for call in notify.call_args_list],
            [
                "workflow_started",
                "sync_completed",
                "clean_skipped",
                "publish_skipped",
                "workflow_completed",
            ],
        )


if __name__ == "__main__":
    unittest.main()
