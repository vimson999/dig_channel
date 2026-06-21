import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1 import celery_monitor  # noqa: E402


class CeleryMonitorWatchdogTest(unittest.TestCase):
    def _status(self, business_tasks):
        service = celery_monitor.SERVICES["report_audio_workflow"]
        return celery_monitor._service_status(
            "report_audio_workflow",
            service,
            inspect_data={
                "report_audio_workflow": {
                    "active": [],
                    "reserved": [],
                    "scheduled": [],
                }
            },
            inspect_error=None,
            queue_lengths={"report_audio_workflow": (10, None)},
            all_processes=[
                {
                    "pid": 12345,
                    "etime": "01:00",
                    "command": "celery -A celery_config.celery_app worker -Q report_audio_workflow --hostname=report_audio_workflow@host",
                }
            ],
            business_tasks=business_tasks,
            business_task_error=None,
        )

    def test_recent_business_task_suppresses_consumer_warning(self):
        status = self._status([
            {
                "id": "job-1",
                "status": "processing",
                "step": "pub_queued",
                "progress": 95,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
        ])

        self.assertIsNone(status["consumer_warning"])
        self.assertIn("业务任务近期仍在推进", status["consumer_note"])

    def test_stale_or_pending_business_tasks_still_warn(self):
        old = datetime.utcnow() - timedelta(seconds=celery_monitor.BUSINESS_TASK_CONSUMER_GRACE_SECONDS_DEFAULT + 120)
        status = self._status([
            {
                "id": "job-1",
                "status": "processing",
                "step": "gem_wait_browser_pro",
                "progress": 11,
                "updated_at": old.isoformat() + "Z",
            },
            {
                "id": "job-2",
                "status": "pending",
                "step": "queued",
                "progress": 20,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
        ])

        self.assertIn("Worker 在线但未领取积压任务", status["consumer_warning"])
        self.assertIsNone(status["consumer_note"])


if __name__ == "__main__":
    unittest.main()
