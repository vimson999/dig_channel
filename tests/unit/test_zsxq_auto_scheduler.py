import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from run_zsxq_auto_scheduler import (  # noqa: E402
    ALL_TABS_SYNC_SCHEDULE_TAG,
    DEFAULT_PROJECT_ROOT,
    SYSTEM_SYNC_SCHEDULE_TAG,
    ZsxqAutoScheduler,
    _skip_startup_trigger,
    _stdout_is_redirected_to_log,
)
import run_zsxq_auto_scheduler as scheduler_module  # noqa: E402
from services import zsxq_system_sync_scheduler as process_scheduler  # noqa: E402


class ZsxqAutoSchedulerTest(unittest.TestCase):
    def test_default_project_root_is_the_repository_root(self):
        self.assertEqual(DEFAULT_PROJECT_ROOT, Path(__file__).resolve().parents[2])

    def test_shell_entrypoint_exports_the_canonical_project_root(self):
        script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "dev"
            / "run-zsxq-auto-scheduler.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('export ZSXQ_PROJECT_ROOT="$PROJECT_ROOT"', script)

    def test_maintenance_reload_can_skip_only_the_startup_trigger(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_skip_startup_trigger())
        with patch.dict(os.environ, {"ZSXQ_AUTO_SCHEDULER_SKIP_STARTUP_TRIGGER": "1"}):
            self.assertTrue(_skip_startup_trigger())
        with patch.dict(os.environ, {"ZSXQ_AUTO_SCHEDULER_SKIP_STARTUP_TRIGGER": "false"}):
            self.assertFalse(_skip_startup_trigger())

    def test_managed_stdout_disables_the_duplicate_stream_log_path(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_stdout_is_redirected_to_log())
        with patch.dict(os.environ, {"SCHEDULER_STDOUT_REDIRECTED_TO_LOG": "1"}):
            self.assertTrue(_stdout_is_redirected_to_log())

    def test_refresh_schedule_registers_frontier_and_three_hour_all_tabs_jobs(self):
        scheduler = ZsxqAutoScheduler.__new__(ZsxqAutoScheduler)
        scheduler.interval_minutes = 240
        scheduler._interval_minutes = lambda: 240
        scheduler.queue_once = lambda: None
        scheduler.queue_all_tabs_once = lambda: None
        scheduler._scheduled_all_tabs_degraded = None
        scheduler._all_tabs_configs = lambda: [
            {
                "key": "shengcai",
                "group_name": "生财有术",
                "interval_minutes": 180,
                "topic_count": 40,
                "updated_at": "2026-07-24T00:00:00Z",
                "source": "database",
            },
            {
                "key": "honghao",
                "group_name": "洪灝的宏观策略",
                "interval_minutes": 180,
                "topic_count": 20,
                "updated_at": "2026-07-24T00:00:00Z",
                "source": "database",
            },
        ]
        try:
            with patch.object(
                scheduler_module,
                "record_all_tabs_schedule_plan",
            ) as record_plan:
                scheduler.refresh_schedule()

            system_jobs = scheduler_module.schedule.get_jobs(SYSTEM_SYNC_SCHEDULE_TAG)
            all_tabs_jobs = scheduler_module.schedule.get_jobs(ALL_TABS_SYNC_SCHEDULE_TAG)
            self.assertEqual(len(system_jobs), 1)
            self.assertEqual(len(all_tabs_jobs), 2)
            self.assertEqual(system_jobs[0].interval, 240)
            self.assertEqual(
                [job.interval for job in all_tabs_jobs],
                [180, 180],
            )
            self.assertEqual(
                {
                    job.job_func.keywords["schedule_key"]
                    for job in all_tabs_jobs
                },
                {"shengcai", "honghao"},
            )
            plans = record_plan.call_args.args[0]
            self.assertEqual(
                {
                    (plan["key"], plan["topic_count"])
                    for plan in plans
                },
                {("shengcai", 40), ("honghao", 20)},
            )
        finally:
            scheduler_module.schedule.clear(SYSTEM_SYNC_SCHEDULE_TAG)
            scheduler_module.schedule.clear(ALL_TABS_SYNC_SCHEDULE_TAG)

    def test_database_recovery_refreshes_schedule_and_catches_up_immediately(self):
        scheduler = ZsxqAutoScheduler.__new__(ZsxqAutoScheduler)
        scheduler.interval_minutes = 240
        scheduler._scheduled_interval = 240
        scheduler._scheduled_all_tabs_signature = (
            ("shengcai", 180, None, "bootstrap_fallback"),
        )
        scheduler._scheduled_all_tabs_degraded = True
        scheduler._interval_minutes = lambda: 240
        database_configs = [
            {
                "key": "shengcai",
                "interval_minutes": 180,
                "updated_at": "2026-07-24T00:00:00Z",
                "source": "database",
            },
        ]
        scheduler._all_tabs_configs = lambda: database_configs
        scheduler.refresh_schedule = unittest.mock.Mock(
            side_effect=lambda **kwargs: setattr(
                scheduler,
                "_scheduled_all_tabs_degraded",
                False,
            )
        )
        scheduler.queue_all_tabs_once = unittest.mock.Mock(
            return_value={
                "queued": True,
                "skipped": False,
                "targets": [{"key": "shengcai", "queued": True}],
            }
        )

        result = scheduler.refresh_runtime_schedule()

        scheduler.refresh_schedule.assert_called_once_with(
            all_tabs_configs=database_configs
        )
        scheduler.queue_all_tabs_once.assert_called_once_with(
            trigger="scheduler_db_recovered"
        )
        self.assertTrue(result["queued"])

    def test_normal_schedule_refresh_does_not_trigger_recovery_catch_up(self):
        scheduler = ZsxqAutoScheduler.__new__(ZsxqAutoScheduler)
        scheduler.interval_minutes = 240
        scheduler._scheduled_interval = 240
        scheduler._scheduled_all_tabs_signature = None
        scheduler._scheduled_all_tabs_degraded = None
        scheduler._interval_minutes = lambda: 240
        database_configs = [
            {
                "key": "honghao",
                "interval_minutes": 180,
                "updated_at": "2026-07-24T00:00:00Z",
                "source": "database",
            },
        ]
        scheduler._all_tabs_configs = lambda: database_configs
        scheduler.refresh_schedule = unittest.mock.Mock()
        scheduler.queue_all_tabs_once = unittest.mock.Mock()

        result = scheduler.refresh_runtime_schedule()

        scheduler.refresh_schedule.assert_called_once_with(
            all_tabs_configs=database_configs
        )
        scheduler.queue_all_tabs_once.assert_not_called()
        self.assertIsNone(result)

    def test_zombie_scheduler_pid_is_not_reported_as_running(self):
        process = unittest.mock.Mock()
        process.status.return_value = "zombie"
        with patch("psutil.Process", return_value=process), patch.object(
            process_scheduler.os,
            "kill",
        ) as os_kill:
            self.assertFalse(process_scheduler._is_pid_running(12345))

        os_kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
