import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "dev" / "check-worker-app-factory-boundary.py"


class WorkerAppFactoryBoundaryTest(unittest.TestCase):
    def _run_gate(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            task_file = Path(directory) / "sample_task.py"
            task_file.write_text(source, encoding="utf-8")
            return subprocess.run(
                [str(ROOT / "venv" / "bin" / "python"), str(SCRIPT), str(task_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_rejects_background_task_that_registers_all_web_apis(self):
        result = self._run_gate(
            "from app import create_app\n\n"
            "def run_task():\n"
            "    return create_app()\n"
        )

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("create_app must set register_apis=False", result.stdout)

    def test_accepts_background_task_with_minimal_app_factory(self):
        result = self._run_gate(
            "from app import create_app\n\n"
            "def run_task():\n"
            "    return create_app(register_apis=False)\n"
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("worker app factory boundary passed", result.stdout)

    def test_repository_background_tasks_use_minimal_app_factory(self):
        result = subprocess.run(
            [str(ROOT / "venv" / "bin" / "python"), str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
