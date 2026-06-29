import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from apis.v1 import celery_monitor  # noqa: E402


class CeleryMonitorWatchdogTest(unittest.TestCase):
    def test_script_material_workflow_service_is_registered(self):
        service = celery_monitor.SERVICES["report_script_material_workflow"]

        self.assertEqual(service["queue"], "report_script_material_workflow")
        self.assertEqual(
            celery_monitor.BUSINESS_TASK_FEATURES["report_script_material_workflow"],
            ("report_script_material_workflow",),
        )
        self.assertIn("report_script_material_workflow", celery_monitor.DEFAULT_WATCHDOG_SERVICES)

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

    def test_skipped_inspect_does_not_suppress_consumer_warning(self):
        service = celery_monitor.SERVICES["report_script_material_workflow"]
        status = celery_monitor._service_status(
            "report_script_material_workflow",
            service,
            inspect_data={},
            inspect_error="skipped",
            queue_lengths={"report_script_material_workflow": (7, None)},
            all_processes=[
                {
                    "pid": 12345,
                    "etime": "01:00",
                    "command": "celery -A celery_config.celery_app worker -Q report_script_material_workflow --hostname=report_script_material_workflow@host",
                }
            ],
            business_tasks=[
                {
                    "id": "job-1",
                    "status": "pending",
                    "step": "queued",
                    "progress": 0,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            ],
            business_task_error=None,
        )

        self.assertEqual(status["inspect_error"], "skipped")
        self.assertIn("Worker 在线但未领取积压任务", status["consumer_warning"])

    def test_paused_services_are_excluded_from_stale_task_repair(self):
        with patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={"content_pipeline": {"reason": "manual_pause"}},
        ):
            self.assertEqual(
                celery_monitor._paused_business_features(),
                {"content_pdf_clean", "content_audio_clean"},
            )

    def test_watchdog_passes_business_tasks_into_service_status(self):
        captured = {}

        def fake_service_status(service_id, service, **kwargs):
            captured["service_id"] = service_id
            captured["business_tasks"] = kwargs.get("business_tasks")
            captured["business_task_error"] = kwargs.get("business_task_error")
            return {
                "consumer_warning": None,
                "queue_length": 1,
            }

        business_task = {
            "id": "pub-1",
            "status": "processing",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        with patch.object(celery_monitor, "_ensure_dirs"), \
            patch.object(celery_monitor, "_watchdog_config", return_value={
                "enabled": True,
                "notify_enabled": False,
                "requeue_stale_tasks": True,
                "services": ["render"],
                "confirmations": 3,
                "cooldown_seconds": 900,
            }), \
            patch.object(celery_monitor, "_load_watchdog_state", return_value={"services": {}, "events": []}), \
            patch.object(celery_monitor, "_save_watchdog_state"), \
            patch.object(celery_monitor, "_watchdog_repair_stale_business_tasks", return_value={"requeued": 0}), \
            patch.object(celery_monitor, "_inspect_tasks_by_queue", return_value=({"render": {"active": [], "reserved": [], "scheduled": []}}, None)), \
            patch.object(celery_monitor, "_business_tasks_snapshot", return_value=({"render": [business_task]}, {"render": None})), \
            patch.object(celery_monitor, "_load_paused_services", return_value={}), \
            patch.object(celery_monitor, "_service_status", side_effect=fake_service_status):
            result = celery_monitor.run_watchdog_once()

        self.assertEqual(result["results"][0]["service_id"], "render")
        self.assertEqual(captured["service_id"], "render")
        self.assertEqual(captured["business_tasks"], [business_task])
        self.assertIsNone(captured["business_task_error"])

    def test_paused_render_skips_publisher_repair(self):
        with patch.object(
            celery_monitor,
            "_load_paused_services",
            return_value={"render": {"reason": "manual_pause"}},
        ), patch(
            "services.publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once"
        ) as cleanup_publisher, patch(
            "services.publisher_queue_repair.repair_stale_publisher_pending_tasks_once"
        ) as repair_pending, patch(
            "services.publisher_queue_repair.repair_stale_publisher_processing_tasks_once"
        ) as repair_processing, patch(
            "services.publisher_queue_repair.repair_stale_publication_tasks_once",
            return_value={"enabled": True, "requeued": 0, "items": []},
        ) as repair_generic:
            result = celery_monitor._watchdog_repair_stale_business_tasks({
                "enabled": True,
                "requeue_stale_tasks": True,
            })

        cleanup_publisher.assert_not_called()
        repair_pending.assert_not_called()
        repair_processing.assert_not_called()
        repair_generic.assert_called_once_with(excluded_features={"publisher"})
        self.assertEqual(result["publisher_pending_repair"]["skipped"], "publisher_paused")
        self.assertEqual(result["processing_repair"]["skipped"], "publisher_paused")


if __name__ == "__main__":
    unittest.main()
