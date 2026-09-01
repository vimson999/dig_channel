import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PdfMergeWorkflowFrontendTest(unittest.TestCase):
    def test_polling_is_single_flight_and_only_runs_for_active_jobs(self):
        contract = PROJECT_ROOT / "tests/frontend/pdf-merge-polling.test.cjs"
        completed = subprocess.run(
            ["node", str(contract)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
