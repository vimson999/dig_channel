import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYZER = PROJECT_ROOT / "scripts" / "dev" / "analyze-publication-task-queries.py"


class PublicationTaskQueryAnalyzerTest(unittest.TestCase):
    def test_production_audit_excludes_pytest_but_can_include_it_explicitly(self):
        content = "\n".join((
            "2026-07-18 15:01:32,227 - publication_tasks_query - WARNING - "
            "publication_tasks SQL categories=multirow_payload fingerprint=aaaaaaaaaaaaaaaa "
            "duration_ms=0.37 pid=60163 role=unknown process=pytest "
            "process_started_at=2026-07-18T07:01:12Z origin=tests/test_demo.py:1:test_demo "
            "error_type=none sql=SELECT publication_tasks.params FROM publication_tasks",
            "2026-07-18 15:22:16,137 - publication_tasks_query - WARNING - "
            "publication_tasks SQL categories=slow_payload,slow fingerprint=bbbbbbbbbbbbbbbb "
            "duration_ms=753.58 pid=8307 role=web process=app.py "
            "process_started_at=2026-07-18T06:06:08Z origin=api/demo.py:1:list_demo "
            "error_type=none sql=SELECT publication_tasks.result FROM publication_tasks",
            "",
        ))
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "queries.log"
            log_path.write_text(content, encoding="utf-8")
            base = [
                sys.executable,
                str(ANALYZER),
                "--since",
                "2026-07-18 00:00:00",
                str(log_path),
            ]
            production = subprocess.run(base, check=True, capture_output=True, text=True)
            with_tests = subprocess.run(
                [*base[:-1], "--include-tests", str(log_path)],
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn("records=1", production.stdout)
        self.assertIn("excluded_tests=1", production.stdout)
        self.assertIn("processes=app.py", production.stdout)
        self.assertNotIn("processes=pytest", production.stdout)
        self.assertIn("records=2", with_tests.stdout)
        self.assertIn("excluded_tests=0", with_tests.stdout)
        self.assertIn("processes=pytest", with_tests.stdout)

    def test_legacy_scalar_json_projection_is_not_reported_as_payload_or_predicate(self):
        content = (
            "2026-07-21 15:49:00,000 - slow_query - WARNING - "
            "慢查询 [scalar] 750.00ms: "
            "SELECT publication_tasks.id, "
            "CAST(publication_tasks.params ->> %(params_1)s AS VARCHAR) AS task_title "
            "FROM publication_tasks "
            "WHERE publication_tasks.feature = %(feature_1)s\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "slow.log"
            log_path.write_text(content, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ANALYZER),
                    "--since",
                    "2026-07-21 00:00:00",
                    str(log_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn("records=1", result.stdout)
        self.assertIn("categories=slow", result.stdout)
        self.assertNotIn("multirow_payload", result.stdout)
        self.assertNotIn("json_predicate", result.stdout)


if __name__ == "__main__":
    unittest.main()
