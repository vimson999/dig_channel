import sys
import tempfile
import unittest
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
        self.assertIn("下次计划：2026-06-25T00:00:00Z", body)


def _patched_paths(root: Path):
    runtime = root / "runtime"
    logs = root / "logs"
    return patch.multiple(
        scheduler_service,
        RUNTIME_DIR=runtime,
        CONFIG_PATH=runtime / "content_retention_cleanup_config.json",
        PID_PATH=runtime / "content_retention_cleanup_scheduler.pid",
        LOG_PATH=logs / "content_retention_cleanup_scheduler.log",
    )


if __name__ == "__main__":
    unittest.main()
