import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "diagnostics"
    / "analyze_db_performance_logs.py"
)


def load_analyzer():
    spec = importlib.util.spec_from_file_location("db_performance_log_analyzer", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DbPerformanceLogAnalyzerTest(unittest.TestCase):
    def test_parses_multiline_slow_query_record(self):
        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "slow_query.log"
            path.write_text(
                "2026-07-29 06:00:49,433 - slow_query - WARNING - "
                "慢查询 [id] 3450.41ms "
                "[pid=70021 role=web origin=services/example.py:12:load]: "
                "SELECT id FROM publication_tasks\n"
                "WHERE publication_tasks.id = %(id_1)s\n",
                encoding="utf-8",
            )

            records = list(analyzer.slow_records(path))

        self.assertEqual(1, len(records))
        _timestamp, duration, origin, pid, record = records[0]
        self.assertEqual(3450.41, duration)
        self.assertEqual("services/example.py:12:load", origin)
        self.assertEqual("70021", pid)
        self.assertIn("WHERE publication_tasks.id", record)

    def test_classifies_connection_and_timeout_events(self):
        analyzer = load_analyzer()

        self.assertEqual(
            "connection_held",
            analyzer.event_type(
                "db_connection_lifecycle duration_ms=5001 pid=1"
            ),
        )
        self.assertEqual(
            "server_closed",
            analyzer.event_type("server closed the connection unexpectedly"),
        )
        self.assertEqual(
            "statement_timeout",
            analyzer.event_type("canceling statement due to statement timeout"),
        )
        self.assertEqual(
            "query_timeout",
            analyzer.event_type("closing because: query timeout (age=60s)"),
        )
        self.assertEqual(
            "connection_timeout",
            analyzer.event_type("connection timed out"),
        )
        self.assertIsNone(
            analyzer.event_type(
                "SELECT * FROM pg_stat_activity WHERE state = 'idle in transaction'"
            )
        )

    def test_parses_native_celery_bracket_timestamp(self):
        analyzer = load_analyzer()

        match = analyzer.TIMESTAMP.match(
            "[2026-07-30 04:15:55,872: WARNING/MainProcess] database error"
        )

        self.assertIsNotNone(match)
        self.assertEqual("2026-07-30 04:15:55", match.group("value"))

    def test_dedicated_publication_query_log_is_in_default_event_logs(self):
        analyzer = load_analyzer()

        self.assertIn(
            analyzer.ROOT / "logs" / "publication_tasks_query.log",
            analyzer.DEFAULT_EVENT_LOGS,
        )

    def test_excludes_explicit_test_sql(self):
        analyzer = load_analyzer()

        self.assertTrue(
            analyzer.is_test_record(
                "role=test process=pytest dialect=sqlite sql=SELECT 1"
            )
        )
        self.assertFalse(
            analyzer.is_test_record(
                "role=web process=app.py dialect=postgresql sql=SELECT 1"
            )
        )


if __name__ == "__main__":
    unittest.main()
