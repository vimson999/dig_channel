import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from run_zsxq_auto_scheduler import (  # noqa: E402
    DEFAULT_PROJECT_ROOT,
    _skip_startup_trigger,
    _stdout_is_redirected_to_log,
)


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


if __name__ == "__main__":
    unittest.main()
