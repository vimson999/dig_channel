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
BOTTLENECK_SCRIPT_PATH = SCRIPT_PATH.with_name("db_bottleneck_report.py")


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
        _timestamp, duration, origin, pid, request_id, record = records[0]
        self.assertEqual(3450.41, duration)
        self.assertEqual("services/example.py:12:load", origin)
        self.assertEqual("70021", pid)
        self.assertEqual("id", request_id)
        self.assertIn("WHERE publication_tasks.id", record)

    def test_normalizes_entity_ids_without_collapsing_api_version(self):
        analyzer = load_analyzer()

        self.assertEqual(
            "/api/v1/jobs/:id/items/:id",
            analyzer.normalize_endpoint(
                "/api/v1/jobs/cd9fde1c-694e-4c61-b236-e7e399348688/items/42"
            ),
        )

    def test_parses_request_start_and_end(self):
        analyzer = load_analyzer()
        start = analyzer.REQUEST_START.search(
            "2026-08-02 03:07:58,900 - middleware.request_logger - INFO - "
            "🌐 [a6e07d65] GET /api/v1/celery/status - IP: 127.0.0.1"
        )
        end = analyzer.REQUEST_END.search(
            "2026-08-02 03:07:58,973 - middleware.request_logger - INFO - "
            "✅ [a6e07d65] 200 - 1649.46ms"
        )

        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertEqual("/api/v1/celery/status", start.group("path"))
        self.assertEqual("1649.46", end.group("duration"))
        warning = analyzer.REQUEST_END.search(
            "2026-08-02 03:07:58,973 - middleware.request_logger - WARNING - "
            "⚠️ [a6e07d65] 401 - 14.98ms"
        )
        self.assertIsNotNone(warning)
        self.assertEqual("401", warning.group("status"))

    def test_parses_celery_outcomes_and_runtime(self):
        analyzer = load_analyzer()
        success = analyzer.CELERY_SUCCESS.search(
            "[2026-08-02 10:28:14,513: INFO/MainProcess] "
            "Task tasks.publisher.upload[task-id] succeeded in 124.17s: None"
        )
        retry = analyzer.CELERY_RETRY.search(
            "[2026-08-02 10:29:14,513: INFO/MainProcess] "
            "Task tasks.sync.run[retry-id] retry: Retry in 60s"
        )
        failure = analyzer.CELERY_FAILURE.search(
            "[2026-08-02 10:30:14,513: ERROR/MainProcess] "
            "Task tasks.render.run[failed-id] raised unexpected: RuntimeError()"
        )

        self.assertEqual("tasks.publisher.upload", success.group("task"))
        self.assertEqual("124.17", success.group("duration"))
        self.assertEqual("tasks.sync.run", retry.group("task"))
        self.assertEqual("tasks.render.run", failure.group("task"))

    def test_parses_idle_in_transaction_pressure_evidence(self):
        analyzer = load_analyzer()
        defer = analyzer.IDLE_PRESSURE_DEFER.search(
            "[2026-08-05 14:27:20,312: WARNING/MainProcess] "
            "[TASK_GUARD] defer task start | task=task-id feature=publisher "
            "queue=render countdown=180s reason=db_idle_in_transaction_pressure"
        )
        pressure = analyzer.IDLE_PRESSURE_COUNT.search(
            "'pressure': {'idle_in_transaction_connections': 5, 'free_connections': 81}"
        )
        retry_pressure = analyzer.IDLE_PRESSURE_COUNT.search(
            "TaskAdmissionDenied('free=80, used=0.1753, idle_tx=8')"
        )

        self.assertEqual("task-id", defer.group("task"))
        self.assertEqual("5", pressure.group("count"))
        self.assertEqual("8", retry_pressure.group("count"))

    def test_parses_connection_hold_attribution_and_sql_breakdown(self):
        analyzer = load_analyzer()
        line = (
            "db_connection_lifecycle duration_ms=5329.42 pid=34298 role=worker "
            "process=celery task_id=task-1 task_name=tasks.image request_id=req-1 "
            "checkout_origin=tasks/report_stick_figure_assets.py:241:_generate_scene_image "
            "sql_count=1 sql_total_ms=205.39 sql_max_ms=205.39 "
            "non_sql_hold_ms=5124.03"
        )
        fields = {
            match.group("name"): match.group("value")
            for match in analyzer.FIELD.finditer(line)
        }

        self.assertEqual("task-1", fields["task_id"])
        self.assertEqual("tasks.image", fields["task_name"])
        self.assertEqual(
            "tasks/report_stick_figure_assets.py:241:_generate_scene_image",
            fields["checkout_origin"],
        )
        self.assertEqual("205.39", fields["sql_total_ms"])
        self.assertEqual("5124.03", fields["non_sql_hold_ms"])

    def test_bottleneck_diagnostics_are_read_only_and_use_feature_projection(self):
        text = BOTTLENECK_SCRIPT_PATH.read_text(encoding="utf-8")
        publisher = text.split(
            '"publisher task list with feature projection"', 1
        )[1].split('"content pipeline task list"', 1)[0]
        content = text.split('"content pipeline task list"', 1)[1].split(
            '"recent publication tasks with large JSON columns"', 1
        )[0]

        self.assertIn('conn.exec_driver_sql("SET TRANSACTION READ ONLY")', text)
        self.assertIn("WHERE feature = 'publisher'", publisher)
        self.assertIn("WHERE feature IN", content)
        self.assertNotIn("params ->> 'feature'", publisher + content)

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

    def test_classifies_pgbouncer_hba_rejections_without_closing_duplicates(self):
        analyzer = load_analyzer()

        self.assertEqual(
            {"server_login_failed", "hba_rejected"},
            analyzer.pgbouncer_event_types(
                'WARNING server login failed: FATAL no pg_hba.conf entry for host "1.2.3.4"'
            ),
        )
        self.assertEqual(
            {"cached_server_login_failure"},
            analyzer.pgbouncer_event_types(
                "WARNING pooler error: server login has been failing, cached error"
            ),
        )
        self.assertEqual(
            set(),
            analyzer.pgbouncer_event_types(
                "LOG closing because: no pg_hba.conf entry"
            ),
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

    def test_discovers_plain_text_and_recent_gzip_rotations(self):
        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "scheduler.log"
            rotated = root / "scheduler.log.1"
            compressed = root / "scheduler.log.2.gz"
            active.write_text("active", encoding="utf-8")
            rotated.write_text("rotated", encoding="utf-8")
            compressed.write_bytes(b"compressed")
            analyzer.DEFAULT_EVENT_LOGS = (active,)
            analyzer.ROOT = root

            paths = analyzer.discover_event_logs()

        self.assertIn(active.resolve(), paths)
        self.assertIn(rotated.resolve(), paths)
        self.assertIn(compressed.resolve(), paths)

    def test_reads_gzip_log_as_text(self):
        import gzip

        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backend.log.1.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("2026-08-04 01:59:59,000 - sample\n")

            with analyzer.open_log(path) as handle:
                content = handle.read()

        self.assertIn("2026-08-04 01:59:59", content)

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

    def test_classifies_adhoc_diagnostic_context_separately(self):
        analyzer = load_analyzer()

        self.assertTrue(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "unknown", "process": "-c"},
                origin="unknown",
            )
        )
        self.assertTrue(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "unknown", "process": "-"},
                origin="unknown",
            )
        )
        self.assertFalse(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "scheduler", "process": "celery_watchdog_loop.py"},
                origin="apis/v1/celery_monitor.py:2307:query",
            )
        )
        self.assertFalse(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "unknown", "process": "-c"},
                origin="scripts/managed_check.py:12:query",
            )
        )


if __name__ == "__main__":
    unittest.main()
