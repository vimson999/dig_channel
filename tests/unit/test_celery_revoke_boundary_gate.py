import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "dev" / "check-celery-revoke-boundary.py"
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"


class CeleryRevokeBoundaryGateTest(unittest.TestCase):
    def _run_gate(self, backend_root):
        self.assertTrue(
            GATE_SCRIPT.is_file(),
            "缺少可执行的 Celery revoke Session 边界门禁",
        )
        return subprocess.run(
            [
                sys.executable,
                str(GATE_SCRIPT),
                "--backend-root",
                str(backend_root),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _write_fixture(root, relative_path, source):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def test_gate_rejects_direct_control_revoke_outside_runtime_guard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_root = Path(temp_dir) / "src" / "backend"
            self._write_fixture(
                backend_root,
                "services/runtime_guard.py",
                "celery_app.control.revoke('allowed-inside-guard')\n",
            )
            self._write_fixture(
                backend_root,
                "api/unsafe_cancel.py",
                "celery_app.control.revoke('unsafe-direct-call')\n",
            )

            result = self._run_gate(backend_root)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("api/unsafe_cancel.py:1", result.stdout)
        self.assertIn("direct control.revoke", result.stdout)

    def test_gate_requires_business_task_identity_on_shared_helper_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_root = Path(temp_dir) / "src" / "backend"
            self._write_fixture(
                backend_root,
                "services/runtime_guard.py",
                "def revoke_celery_task_after_db_commit(*args, **kwargs): pass\n",
            )
            self._write_fixture(
                backend_root,
                "api/unobservable_cancel.py",
                (
                    "from services.runtime_guard import "
                    "revoke_celery_task_after_db_commit\n"
                    "revoke_celery_task_after_db_commit(\n"
                    "    'broker-id', operation='workflow cancel'\n"
                    ")\n"
                ),
            )

            result = self._run_gate(backend_root)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("api/unobservable_cancel.py:2", result.stdout)
        self.assertIn("business_task_id", result.stdout)

    def test_current_backend_satisfies_revoke_boundary_gate(self):
        result = self._run_gate(BACKEND_ROOT)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK: Celery revoke boundary contract", result.stdout)


if __name__ == "__main__":
    unittest.main()
