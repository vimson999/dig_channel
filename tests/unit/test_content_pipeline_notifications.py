import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline.notification_service import (  # noqa: E402
    notify_content_pipeline_event,
    pipeline_metadata,
)


class ContentPipelineNotificationTest(unittest.TestCase):
    def test_direct_pipeline_uses_common_workflow_email_contract(self):
        with patch(
            "services.workflow_notification_service.workflow_notification_service.enqueue_workflow_event",
            return_value={"queued": True, "celery_task_id": "notify-1"},
        ) as enqueue:
            result = notify_content_pipeline_event(
                "clean_completed",
                task_id="clean-1",
                task_label="PDF 清洗",
                pipeline={
                    "title": "手动 PDF",
                    "trigger": "manual_download",
                    "metadata": {
                        "orchestration_id": "content-pipeline-batch-1",
                        "source_mode": "manual_download",
                        "group_name": "外资研报",
                    },
                },
                result={"cleaned_files": [{"path": "/tmp/a.pdf"}]},
            )

        self.assertTrue(result["queued"])
        payload = enqueue.call_args.args[1]
        self.assertEqual(payload["id"], "content-pipeline-batch-1")
        self.assertEqual(payload["source_mode"], "manual_download")
        self.assertEqual(payload["stage_results"]["clean"]["status"], "completed")
        self.assertEqual(payload["stage_results"]["clean"]["tasks"][0]["success_count"], 1)

    def test_workflow_owned_pipeline_does_not_duplicate_notification(self):
        with patch(
            "services.workflow_notification_service.workflow_notification_service.enqueue_workflow_event"
        ) as enqueue:
            result = notify_content_pipeline_event(
                "clean_submitted",
                task_id="clean-1",
                pipeline={"metadata": {"workflow_run_id": "workflow-1"}},
            )

        self.assertEqual(result, {"queued": False, "reason": "workflow_owned"})
        enqueue.assert_not_called()

    def test_source_metadata_is_flattened_for_publish_child(self):
        metadata = pipeline_metadata(
            {
                "metadata": {
                    "source_metadata": {
                        "workflow_run_id": "workflow-1",
                        "group_name": "外资研报",
                    },
                    "content_pipeline_task_id": "clean-1",
                }
            }
        )
        self.assertEqual(metadata["workflow_run_id"], "workflow-1")
        self.assertEqual(metadata["content_pipeline_task_id"], "clean-1")


if __name__ == "__main__":
    unittest.main()
