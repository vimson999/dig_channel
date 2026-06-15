import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import services.report_video_asset_scheduler_service as scheduler_service  # noqa: E402


class ReportVideoAssetSchedulerServiceTest(unittest.TestCase):
    def test_default_config_targets_previous_day_and_external_report_tab(self):
        config = scheduler_service.default_config()
        payload = scheduler_service.build_daily_backfill_payload(config, trigger="unit_test", target_date="2026-06-13")

        self.assertEqual(payload["source"], "daily_backfill")
        self.assertEqual(payload["target_date"], "2026-06-13")
        self.assertTrue(payload["parse_pdfs"])
        self.assertEqual(payload["config"]["max_files"], 50)
        self.assertEqual(payload["config"]["tab_label_keyword"], "外资研报")

    def test_save_config_normalizes_bounds_and_persists_secure_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_runtime = scheduler_service.RUNTIME_DIR
            old_config = scheduler_service.CONFIG_PATH
            old_pid = scheduler_service.PID_PATH
            old_log = scheduler_service.LOG_PATH
            scheduler_service.RUNTIME_DIR = root / "runtime"
            scheduler_service.CONFIG_PATH = scheduler_service.RUNTIME_DIR / "report_video_asset_scan_scheduler_config.json"
            scheduler_service.PID_PATH = scheduler_service.RUNTIME_DIR / "report_video_asset_scan_scheduler.pid"
            scheduler_service.LOG_PATH = root / "logs" / "report_video_asset_scan_scheduler.log"
            try:
                config = scheduler_service.save_config({
                    "enabled": True,
                    "schedule_time": "99:99",
                    "interval_minutes": "0",
                    "target_date_offset_days": 999,
                    "config": {
                        "max_files": 9999,
                        "tab_label_keyword": "外资研报",
                        "output_ratios": ["16:9", "bad"],
                    },
                })

                self.assertEqual(config["schedule_time"], "23:59")
                self.assertIsNone(config["interval_minutes"])
                self.assertEqual(config["target_date_offset_days"], 30)
                self.assertEqual(config["config"]["max_files"], 500)
                self.assertEqual(config["config"]["output_ratios"], ["16:9"])
                self.assertTrue(scheduler_service.CONFIG_PATH.exists())
            finally:
                scheduler_service.RUNTIME_DIR = old_runtime
                scheduler_service.CONFIG_PATH = old_config
                scheduler_service.PID_PATH = old_pid
                scheduler_service.LOG_PATH = old_log


if __name__ == "__main__":
    unittest.main()
