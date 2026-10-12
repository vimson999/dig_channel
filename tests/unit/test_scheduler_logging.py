import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "src" / "backend"


class SchedulerLoggingTest(unittest.TestCase):
    def test_importing_scheduler_api_does_not_attach_production_log_handler(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import logging; from apis.v1 import scheduler; "
                "print([getattr(h, 'baseFilename', '') "
                "for h in logging.getLogger().handlers])",
            ],
            cwd=BACKEND_DIR,
            env=dict(os.environ, PYTHONPATH=str(BACKEND_DIR)),
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertNotIn("scheduler.log", result.stdout)

    def test_importing_scheduler_runner_does_not_attach_production_log_handler(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import logging, run_scheduler; "
                "print([getattr(h, 'baseFilename', '') "
                "for h in logging.getLogger().handlers])",
            ],
            cwd=BACKEND_DIR,
            env=dict(os.environ, PYTHONPATH=str(BACKEND_DIR)),
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertNotIn("scheduler.log", result.stdout)

    def test_scheduler_runner_only_creates_log_directory_during_explicit_configuration(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from unittest.mock import patch; "
                "p = patch('os.makedirs'); m = p.start(); "
                "import run_scheduler; before = list(m.call_args_list); "
                "run_scheduler._configure_logging(); p.stop(); "
                "print(sum('scheduler.log' in getattr(h, 'baseFilename', '') "
                "for h in __import__('logging').getLogger().handlers)); "
                "print(len(before), len(m.call_args_list))",
            ],
            cwd=BACKEND_DIR,
            env=dict(os.environ, PYTHONPATH=str(BACKEND_DIR)),
            capture_output=True,
            text=True,
            check=True,
        )

        handler_count, call_counts = result.stdout.strip().splitlines()[-2:]
        before, after = map(int, call_counts.split())
        self.assertEqual("1", handler_count)
        self.assertEqual(before + 1, after)


if __name__ == "__main__":
    unittest.main()
