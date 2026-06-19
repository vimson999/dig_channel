import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import zsxq_sync  # noqa: E402


class ZsxqWorkflowFinalizerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
