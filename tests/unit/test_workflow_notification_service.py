import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.workflow_notification_service import workflow_notification_service  # noqa: E402


class WorkflowNotificationServiceTest(unittest.TestCase):
    def test_skipped_clean_checks_are_not_reported_as_running(self):
        workflow = {
            "stage_results": {
                "clean": {
                    "status": "skipped",
                    "tasks": [
                        {"queued": False, "reason": "no_downloaded_pdf", "files_count": 0},
                        {"queued": False, "reason": "no_downloaded_audio", "files_count": 0},
                    ],
                }
            }
        }

        lines = workflow_notification_service._stage_lines(workflow)

        self.assertIn("清洗：跳过 | 检查 2 项，跳过 2", lines)
        self.assertNotIn("进行中 2", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
