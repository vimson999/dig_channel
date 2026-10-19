import importlib.util
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "diagnostics"
    / "daily_db_runtime_snapshot.py"
)


def load_snapshot_module():
    spec = importlib.util.spec_from_file_location("daily_db_runtime_snapshot", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DailyDbRuntimeSnapshotTest(unittest.TestCase):
    def test_local_audit_window_is_converted_to_utc_naive_database_bounds(self):
        snapshot = load_snapshot_module()

        self.assertEqual(
            snapshot.utc_naive_database_boundary(
                "2026-10-16 06:00:00",
                timezone_name="Asia/Shanghai",
            ),
            datetime(2026, 10, 15, 22, 0, 0),
        )
        self.assertEqual(
            snapshot.utc_naive_database_boundary(
                "2026-10-17T06:00:00",
                timezone_name="Asia/Shanghai",
            ),
            datetime(2026, 10, 16, 22, 0, 0),
        )

    def test_task_root_summary_covers_non_publisher_features(self):
        snapshot = load_snapshot_module()

        summary = snapshot.summarize_task_roots(
            [
                {
                    "feature": "content_pdf_clean",
                    "old_root": True,
                    "current_root": False,
                },
                {
                    "feature": "content_pdf_clean",
                    "old_root": False,
                    "current_root": True,
                },
                {
                    "feature": "zsxq_clean_output_publish",
                    "old_root": True,
                    "current_root": False,
                },
            ]
        )

        self.assertEqual(
            summary,
            [
                {
                    "feature": "content_pdf_clean",
                    "total": 2,
                    "old_root": 1,
                    "current_root": 1,
                    "unclassified": 0,
                },
                {
                    "feature": "zsxq_clean_output_publish",
                    "total": 1,
                    "old_root": 1,
                    "current_root": 0,
                    "unclassified": 0,
                },
            ],
        )

    def test_publisher_summary_handles_jsonb_values_without_database_json_functions(self):
        snapshot = load_snapshot_module()
        summary = snapshot.summarize_publisher_rows(
            [
                {
                    "status": "completed",
                    "duration_s": 10.0,
                    "old_root": True,
                    "repair_reason": "pending_message_not_active_but_queue_visible",
                    "repair_history": [{"current_celery_id": "new-1"}],
                },
                {
                    "status": "failed",
                    "duration_s": 30.0,
                    "old_root": False,
                    "repair_reason": None,
                    "repair_history": None,
                },
            ]
        )

        self.assertEqual(
            summary,
            {
                "total": 2,
                "completed": 1,
                "failed": 1,
                "canceled": 0,
                "old_root": 1,
                "old_reason": 1,
                "replacement_history": 1,
                "total_s": 40.0,
                "p50_s": 20.0,
                "p95_s": 29.0,
                "max_s": 30.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
