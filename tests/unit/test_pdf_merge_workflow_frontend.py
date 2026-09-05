import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PdfMergeWorkflowFrontendTest(unittest.TestCase):
    def test_backend_requests_use_runtime_api_origin(self):
        script = (
            PROJECT_ROOT / "src/frontend/js/pdf-merge-workflow.js"
        ).read_text(encoding="utf-8")
        html = (
            PROJECT_ROOT / "src/frontend/pdf-merge-workflow.html"
        ).read_text(encoding="utf-8")

        self.assertIn("apiRoot() {", script)
        self.assertIn("fetch(`${this.apiRoot()}${path}`,", script)
        self.assertIn(
            "window.location.assign(`${this.apiRoot()}/api/pdf-merge-workflows/jobs/",
            script,
        )
        self.assertNotIn("fetch(path,", script)
        self.assertIn(
            "js/pdf-merge-workflow.js?v=20260902-api-origin",
            html,
        )

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
