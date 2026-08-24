import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline import retention_scheduler_service as scheduler_service  # noqa: E402
from run_content_retention_cleanup_scheduler import _build_retention_email  # noqa: E402


class ContentRetentionSchedulerServiceTest(unittest.TestCase):
    def test_load_config_returns_safe_defaults_without_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patched_paths(Path(temp_dir)):
                config = scheduler_service.load_config()

            self.assertFalse(config["exists"])
            self.assertTrue(config["enabled"])
            self.assertFalse(config["dry_run"])
            self.assertEqual(config["interval_hours"], 24)
            self.assertEqual(config["policy"]["sync_raw_days"], 3)
            self.assertEqual(config["policy"]["processed_video_days"], 3)
            self.assertTrue(config["policy"]["include_processed_videos"])

    def test_save_config_normalizes_policy_and_preserves_runtime_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with _patched_paths(root):
                scheduler_service.update_runtime_status(
                    {
                        "last_run_at": "2026-06-13T12:00:00Z",
                        "next_run_at": "2026-06-14T12:00:00Z",
                        "last_result": {"candidate_count": 3},
                    }
                )
                config = scheduler_service.save_config(
                    {
                        "enabled": "true",
                        "dry_run": "false",
                        "interval_hours": 9999,
                        "policy": {
                            "sync_raw_days": 3,
                            "include_pytest_batches": False,
                            "max_candidates": 42,
                        },
                    }
                )

            self.assertTrue(config["exists"])
            self.assertFalse(config["dry_run"])
            self.assertEqual(config["interval_hours"], 720)
            self.assertEqual(config["policy"]["sync_raw_days"], 3)
            self.assertFalse(config["policy"]["include_pytest_batches"])
            self.assertEqual(config["policy"]["max_candidates"], 42)
            self.assertEqual(config["last_run_at"], "2026-06-13T12:00:00Z")
            self.assertEqual(config["last_result"]["candidate_count"], 3)

    def test_retention_cleanup_email_contains_summary_and_policy(self):
        subject, body = _build_retention_email(
            "completed",
            trigger="scheduler",
            dry_run=False,
            policy={
                "sync_raw_days": 3,
                "content_pipeline_batch_days": 3,
                "manual_publish_days": 3,
                "pytest_batch_days": 3,
                "processed_video_days": 3,
                "include_processed_videos": True,
                "unfinished_reference_protect_days": 3,
                "max_candidates": 5000,
            },
            summary={
                "candidate_count": 10,
                "total_human": "1.2 GB",
                "deleted_count": 10,
                "deleted_human": "1.2 GB",
                "error_count": 0,
                "elapsed_seconds": 2.5,
                "completed_at": "2026-06-24T00:00:00Z",
            },
            config={"next_run_at": "2026-06-25T00:00:00Z", "log_path": "/tmp/retention.log"},
        )

        self.assertIn("完成", subject)
        self.assertIn("已删除：10", body)
        self.assertIn("未收尾任务保护：3 天", body)
        self.assertIn("生成 MP4：3 天", body)
        self.assertIn("下次计划：2026-06-25T00:00:00Z", body)

    def test_scheduled_next_run_preserves_future_persisted_time(self):
        now = datetime(2026, 7, 1, 1, 0, 0, tzinfo=timezone.utc)
        target = scheduler_service.scheduled_next_run_datetime(
            {
                "interval_hours": 24,
                "next_run_at": "2026-07-01T02:19:00Z",
            },
            now=now,
        )

        self.assertEqual(scheduler_service.utc_datetime_iso(target), "2026-07-01T02:19:00Z")

    def test_scheduled_next_run_preserves_overdue_time_for_catchup(self):
        now = datetime(2026, 7, 1, 3, 0, 0, tzinfo=timezone.utc)
        target = scheduler_service.scheduled_next_run_datetime(
            {
                "interval_hours": 24,
                "next_run_at": "2026-07-01T02:19:00Z",
            },
            now=now,
        )

        self.assertLess(target, now)
        self.assertEqual(scheduler_service.utc_datetime_iso(target), "2026-07-01T02:19:00Z")

    def test_scheduled_next_run_defaults_from_now_when_no_persisted_time(self):
        now = datetime(2026, 7, 1, 1, 0, 0, tzinfo=timezone.utc)
        target = scheduler_service.scheduled_next_run_datetime(
            {"interval_hours": 6},
            now=now,
        )

        self.assertEqual(target, now + timedelta(hours=6))

    def test_zombie_pid_is_not_considered_running(self):
        class Completed:
            returncode = 0
            stdout = "Z\n"

        with patch.object(scheduler_service.subprocess, "run", return_value=Completed()), \
             patch.object(scheduler_service.os, "kill") as kill:
            self.assertFalse(scheduler_service._is_pid_running(12345))

        kill.assert_not_called()

    def test_start_scheduler_skips_when_start_lock_is_held(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with _patched_paths(Path(temp_dir)), \
                 patch.object(scheduler_service, "acquire_scheduler_start_lock", return_value=None):
                result = scheduler_service.start_scheduler()

        self.assertFalse(result["started"])
        self.assertFalse(result["running"])
        self.assertIn("正在启动", result["message"])


def _patched_paths(root: Path):
    runtime = root / "runtime"
    logs = root / "logs"
    return patch.multiple(
        scheduler_service,
        RUNTIME_DIR=runtime,
        CONFIG_PATH=runtime / "content_retention_cleanup_config.json",
        PID_PATH=runtime / "content_retention_cleanup_scheduler.pid",
        START_LOCK_PATH=runtime / "content_retention_cleanup_scheduler_start.lock",
        PROCESS_LOCK_PATH=runtime / "content_retention_cleanup_scheduler_process.lock",
        LOG_PATH=logs / "content_retention_cleanup_scheduler.log",
    )


if __name__ == "__main__":
    unittest.main()
