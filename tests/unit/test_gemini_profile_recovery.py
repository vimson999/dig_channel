import signal
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.gemini_browser import (  # noqa: E402
    GeminiBrowserWorker,
    _recover_orphaned_chrome_profile,
)


class GeminiProfileRecoveryTest(unittest.TestCase):
    def test_recovers_only_orphaned_automation_chrome_for_exact_profile(self):
        profile = Path("/tmp/report_visual_plan/worker_1")
        owner = "host-123（pid 123 运行中）"
        command = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--no-startup-window --use-mock-keychain "
            f"--user-data-dir={profile}"
        )

        with patch(
            "services.gemini_browser._profile_process_info",
            return_value={"pid": 123, "ppid": 1, "command": command},
        ), patch(
            "services.gemini_browser._pid_is_running",
            side_effect=[True, False],
        ), patch("services.gemini_browser.os.kill") as kill:
            recovered = _recover_orphaned_chrome_profile(
                profile,
                owner,
                enabled=True,
            )

        self.assertTrue(recovered)
        kill.assert_called_once_with(123, signal.SIGTERM)

    def test_does_not_touch_non_automation_or_non_orphan_process(self):
        profile = Path("/tmp/report_visual_plan/worker_1")
        command = f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir={profile}"

        with patch(
            "services.gemini_browser._profile_process_info",
            return_value={"pid": 123, "ppid": 99, "command": command},
        ), patch("services.gemini_browser._pid_is_running", return_value=True), patch(
            "services.gemini_browser.os.kill"
        ) as kill:
            recovered = _recover_orphaned_chrome_profile(
                profile,
                "host-123（pid 123 运行中）",
                enabled=True,
            )

        self.assertFalse(recovered)
        kill.assert_not_called()

    def test_profile_wait_invokes_recovery_before_reporting_live_lock(self):
        worker = GeminiBrowserWorker.__new__(GeminiBrowserWorker)
        worker.options = SimpleNamespace(
            user_data_dir="/tmp/report_visual_plan/worker_1",
            auto_recover_orphaned_chrome=True,
        )

        with patch(
            "services.gemini_browser._path_exists_or_symlink",
            side_effect=[True, False],
        ), patch(
            "services.gemini_browser._singleton_owner",
            return_value="host-123（pid 123 运行中）",
        ), patch(
            "services.gemini_browser._recover_orphaned_chrome_profile",
            return_value=True,
        ) as recover:
            worker._wait_for_profile_singleton_release()

        recover.assert_called_once_with(
            Path("/tmp/report_visual_plan/worker_1"),
            "host-123（pid 123 运行中）",
            enabled=True,
        )


if __name__ == "__main__":
    unittest.main()
