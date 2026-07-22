import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import zsxq_sync  # noqa: E402
from models.task import PublicationTask  # noqa: E402
from services.zsxq_workflow_service import zsxq_workflow_service  # noqa: E402


class ZsxqWorkflowFinalizerTest(unittest.TestCase):
    def test_sync_summary_drops_dispatch_items_but_keeps_business_counts(self):
        dispatch = {
            "status": "completed",
            "queued_count": 2,
            "failed_count": 1,
            "queued_task_ids": ["task-1", "task-2"],
            "config": {"prompt": "x" * 10000},
            "items": [{"payload": "x" * 10000}],
            "pdf": {
                "checked": 3,
                "queued": 2,
                "failed": 1,
                "items": [{"payload": "x" * 10000}],
            },
        }

        compact = zsxq_workflow_service._sync_summary({
            "summary": {"auto_script_material_dispatch": dispatch},
        })
        projected = compact["auto_script_material_dispatch"]

        self.assertEqual(projected["status"], "completed")
        self.assertEqual(projected["queued_count"], 2)
        self.assertEqual(projected["failed_count"], 1)
        self.assertEqual(projected["pdf"]["checked"], 3)
        self.assertEqual(projected["pdf"]["failed"], 1)
        self.assertNotIn("config", projected)
        self.assertNotIn("items", projected)
        self.assertNotIn("items", projected["pdf"])

    def test_sync_delivery_compacts_duplicate_result_payload(self):
        result = {
            "run": {
                "id": "run-1",
                "status": "completed",
                "result": {
                    "downloaded_pdf_files": [{"path": "/tmp/large.pdf", "blob": "x" * 1000}],
                    "content_pipeline": {"pdf_clean": {"task_id": "clean-1"}},
                    "report_asset_scan": {"celery_task_id": "asset-1"},
                },
            },
            "summary": {
                "topics_fetched": 78,
                "attachments_downloaded": 4,
                "downloaded_pdf_files": [{"path": "/tmp/large.pdf", "blob": "x" * 1000}],
                "tabs": [
                    {
                        "tab_id": "tab-1",
                        "topics_fetched": 78,
                        "downloaded_pdf_files": [{"path": "/tmp/large.pdf"}],
                    }
                ],
            },
        }

        compact = zsxq_sync._compact_sync_delivery_result(result, run_id="run-1")

        self.assertEqual(compact["summary"]["topics_fetched"], 78)
        self.assertEqual(compact["summary"]["tabs"][0]["tab_id"], "tab-1")
        self.assertNotIn("downloaded_pdf_files", compact["summary"])
        self.assertNotIn("downloaded_pdf_files", compact["summary"]["tabs"][0])
        self.assertEqual(
            compact["run"]["result"]["content_pipeline"]["pdf_clean"]["task_id"],
            "clean-1",
        )

    def test_report_asset_scan_is_delayed_to_avoid_sync_completion_stampede(self):
        from tasks import report_video

        async_result = SimpleNamespace(id="asset-task-id")
        result = {
            "run": {
                "result": {
                    "downloaded_pdf_files": [{"path": "/tmp/report.pdf"}],
                }
            }
        }
        with patch.object(
            report_video.scan_zsxq_report_assets_task,
            "apply_async",
            return_value=async_result,
        ) as apply_async:
            zsxq_sync._submit_report_asset_scan_after_sync(
                "run-1",
                {"report_asset_scan_delay_seconds": 75},
                result,
            )

        apply_async.assert_called_once()
        self.assertEqual(apply_async.call_args.kwargs["countdown"], 75)
        self.assertEqual(
            result["run"]["result"]["report_asset_scan"]["celery_task_id"],
            "asset-task-id",
        )

    def test_verified_runtime_recovers_publish_task_marked_failed_by_db_disconnect(self):
        task = PublicationTask(
            id="publish-1",
            status="failed",
            current_step="failed",
            progress=5,
            error_message="Can't reconnect until invalid transaction is rolled back",
            params={"feature": "zsxq_clean_output_publish", "zsxq_publish": {}},
            result={"message": "publishing"},
        )
        runtime = {
            "summary": {
                "submitted_count": 16,
                "verified_count": 16,
                "failed_count": 0,
                "pending_verify_count": 0,
            },
            "runs": [{"id": "run-1"}, {"id": "run-2"}],
            "batches": [{"run_id": "run-1"}, {"run_id": "run-2"}],
            "topic_ids": ["topic-1", "topic-2"],
        }

        with patch(
            "services.zsxq_workflow_service.summarize_publish_runs_for_request",
            return_value=runtime,
        ), patch.object(
            zsxq_workflow_service,
            "_publish_expected_source_count",
            return_value=16,
        ), patch(
            "services.zsxq_workflow_service.zsxq_sync_service.update_attachment_publish_status_from_publish_task",
        ) as update_attachments:
            recovered = zsxq_workflow_service._recover_publish_task_from_runtime(task)

        self.assertTrue(recovered)
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.current_step, "done")
        self.assertEqual(task.progress, 100)
        self.assertIsNone(task.error_message)
        self.assertTrue(task.result["recovered_from_runtime"])
        self.assertEqual(task.result["summary"]["verified_count"], 16)
        update_attachments.assert_called_once_with(task)

    def test_sync_task_queues_finalizer_instead_of_recording_inline(self):
        async_result = SimpleNamespace(id="finalizer-task-id")
        with patch("tasks.zsxq_workflow.finalize_zsxq_sync_stage.apply_async", return_value=async_result) as apply_async:
            task_id = zsxq_sync._notify_workflow_sync_finished(
                "workflow-1",
                "run-1",
                success=True,
                result={"summary": {"topics_fetched": 3}},
            )

        self.assertEqual(task_id, "finalizer-task-id")
        apply_async.assert_called_once_with(
            args=["workflow-1", "run-1"],
            kwargs={
                "success": True,
                "result": {"summary": {"topics_fetched": 3}},
                "error": None,
            },
            queue="zsxq_workflow",
            retry=False,
        )

    def test_sync_finalizer_queue_failure_returns_none(self):
        with patch("tasks.zsxq_workflow.finalize_zsxq_sync_stage.apply_async", side_effect=RuntimeError("redis down")):
            task_id = zsxq_sync._notify_workflow_sync_finished(
                "workflow-1",
                "run-1",
                success=False,
                error="sync failed",
            )

        self.assertIsNone(task_id)

    def test_clean_submit_disconnect_rolls_back_and_propagates_for_task_retry(self):
        workflow = SimpleNamespace(id="workflow-1")
        disconnect = OperationalError(
            "SELECT 1",
            {},
            Exception("server closed the connection unexpectedly"),
        )

        with patch.object(
            zsxq_workflow_service,
            "_normalize_pdf_cleaner",
            side_effect=lambda value: value,
        ), patch.object(
            zsxq_workflow_service,
            "_submit_one_clean_task",
            wraps=zsxq_workflow_service._submit_one_clean_task,
        ), patch.object(
            zsxq_workflow_service,
            "_now_iso",
            return_value="2026-07-18T00:00:00Z",
        ), patch(
            "services.zsxq_workflow_service.zsxq_sync_service.submit_pdf_clean_for_run",
            side_effect=disconnect,
        ), patch(
            "services.zsxq_workflow_service.db.session.rollback",
        ) as rollback:
            with self.assertRaises(OperationalError):
                zsxq_workflow_service._submit_one_clean_task(
                    workflow=workflow,
                    run_id="run-1",
                    file_type="pdf",
                    cleaner={},
                )

        rollback.assert_called_once()

    def test_clean_stage_keeps_parent_loaded_across_child_commits(self):
        workflow = SimpleNamespace(
            id="workflow-1",
            sync_run_id="run-1",
            config_snapshot={
                "stages": {"clean": True},
                "clean": {"auto_pdf_clean": True, "auto_audio_clean": True},
            },
            stage_results={},
            clean_task_ids=[],
            current_stage="sync",
        )
        session = SimpleNamespace(expire_on_commit=True)
        scoped_session = MagicMock(return_value=session)

        def submit_child(*, file_type, **_kwargs):
            self.assertFalse(session.expire_on_commit)
            return {"queued": True, "task_id": f"{file_type}-task"}

        with patch(
            "services.zsxq_workflow_service.db.session",
            scoped_session,
        ), patch.object(
            zsxq_workflow_service,
            "_normalize_pdf_cleaner",
            side_effect=lambda value: value or {},
        ), patch.object(
            zsxq_workflow_service,
            "_submit_one_clean_task",
            side_effect=submit_child,
        ), patch.object(
            zsxq_workflow_service,
            "_queue_notification",
        ):
            zsxq_workflow_service._submit_clean_stage(workflow)

        self.assertTrue(session.expire_on_commit)
        self.assertEqual(workflow.clean_task_ids, ["pdf-task", "audio-task"])
        self.assertEqual(workflow.stage_results["clean"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
