import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


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
    def test_multiline_traceback_is_one_timestamped_failure_episode(self):
        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scheduler.log"
            path.write_text(
                "2026-09-03 08:54:10,721 - watchdog - ERROR - recovery failed\n"
                "Traceback (most recent call last):\n"
                "  File utils/db_connection_lifecycle.py, line 12\n"
                "psycopg2.OperationalError: server closed the connection unexpectedly\n"
                "sqlalchemy.exc.OperationalError: server closed the connection unexpectedly\n"
                "[SQL: SELECT provider, count(id) FROM workflow_notification_deliveries\n"
                "WHERE status IN (%(status_1)s) GROUP BY provider]\n"
                "2026-09-03 08:55:00,001 - watchdog - INFO - recovered\n",
                encoding="utf-8",
            )
            with path.open() as handle:
                records = list(analyzer.event_records(handle))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0][0], datetime(2026, 9, 3, 8, 54, 10, 721000))
        self.assertEqual(analyzer.event_type(records[0][1]), "server_closed")
        self.assertIn("GROUP BY provider]", records[0][1])
        self.assertIsNone(analyzer.event_type(records[1][1]))

    def test_main_counts_duplicated_traceback_once_not_as_a_connection_hold(self):
        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = (
                "2026-09-03 08:54:10,721 - watchdog - ERROR - recovery failed\n"
                "Traceback (most recent call last):\n"
                "  File utils/db_connection_lifecycle.py, line 12\n"
                "psycopg2.OperationalError: server closed the connection unexpectedly\n"
                "sqlalchemy.exc.OperationalError: server closed the connection unexpectedly\n"
                "[SQL: SELECT provider FROM workflow_notification_deliveries]\n"
            )
            paths = [root / "scheduler.log", root / "celery.log"]
            for path in paths:
                path.write_text(record)
            output = io.StringIO()
            with patch("sys.argv", [str(SCRIPT_PATH), "--since", "2026-09-03 06:00:00", "--until", "2026-09-04 06:00:00", *map(str, paths)]), patch.object(analyzer, "ROOT", root), patch.object(analyzer, "SLOW_LOG", root / "absent.log"), redirect_stdout(output):
                self.assertEqual(analyzer.main(), 0)
        self.assertIn("events connection_held=0 server_closed=1", output.getvalue())
        self.assertIn("sql_error_traceback_episodes count=1", output.getvalue())

    @staticmethod
    def _traceback(at="2026-09-03 08:54:10,100", context="pid=11 role=worker request_id=one"):
        return (
            f"{at} - watchdog - ERROR - failed {context}\n"
            "Traceback (most recent call last):\n"
            "sqlalchemy.exc.OperationalError: server closed the connection unexpectedly\n"
            "[SQL: SELECT provider FROM workflow_notification_deliveries]\n"
            "[parameters: {'secret': 'DO_NOT_PRINT_ME'}]\n"
        )

    def _analyze_events(self, contents, *, until="2026-09-04 06:00:00"):
        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name, content in contents.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                paths.append(path)
            output = io.StringIO()
            with patch("sys.argv", [str(SCRIPT_PATH), "--since", "2026-09-03 06:00:00", "--until", until, *map(str, paths)]), patch.object(
                analyzer, "ROOT", root
            ), patch.object(analyzer, "SLOW_LOG", root / "absent.log"), redirect_stdout(output):
                self.assertEqual(analyzer.main(), 0)
        return output.getvalue()

    def test_independent_failures_within_a_second_are_not_merged(self):
        for later, context in (
            ("2026-09-03 08:54:10,900", "pid=11 role=worker request_id=one"),
            ("2026-09-03 08:54:10,100", "pid=22 role=worker request_id=two"),
        ):
            with self.subTest(later=later, context=context):
                output = self._analyze_events({"scheduler.log": self._traceback() + self._traceback(later, context)})
                self.assertIn("sql_error_traceback_episodes count=2", output)
                self.assertIn("events connection_held=0 server_closed=2", output)
                self.assertNotIn("DO_NOT_PRINT_ME", output)

    def test_mirrored_files_preserve_repeated_occurrences_in_one_source(self):
        record = self._traceback()
        output = self._analyze_events({"scheduler.log": record * 2, "backend.log": record * 2})
        self.assertIn("sql_error_traceback_episodes count=2", output)
        self.assertIn("events connection_held=0 server_closed=2", output)
        self.assertIn("scheduler.log", output)
        self.assertIn("backend.log", output)

    def test_tracebacks_exclude_tests_and_separate_adhoc_diagnostics(self):
        output = self._analyze_events({"scheduler.log": (
            self._traceback(context="pid=1 role=test process=pytest dialect=sqlite")
            + self._traceback(context="pid=2 role=unknown process=-c origin=unknown")
            + self._traceback(context="pid=3 role=worker process=celery")
        )})
        self.assertIn("sql_error_traceback_episodes count=2", output)
        self.assertIn("sql_error_traceback_application count=1", output)
        self.assertIn("sql_error_traceback_adhoc_diagnostics count=1", output)
        self.assertIn("events_application connection_held=0 server_closed=1", output)
        self.assertIn("events_adhoc_diagnostics connection_held=0 server_closed=1", output)

    def test_colored_headers_own_their_window_and_do_not_poison_fields(self):
        in_window = (
            "2026-09-03 23:59:59,100 - lifecycle - WARNING - "
            "db_connection_lifecycle duration_ms=5000 pid=1 role=worker non_sql_hold_ms=5000\n"
        )
        outside = self._traceback().replace(
            "2026-09-03 08:54:10,100 - watchdog - ERROR -",
            "\033[31m❌ [2026-09-04 06:30:00] ERROR    - watchdog -",
        ) + "\033[0m\n"
        output = self._analyze_events({"backend.log": in_window + outside})
        self.assertIn("events connection_held=1 server_closed=0", output)
        self.assertIn("sql_error_traceback_episodes count=0", output)

    def test_unowned_prefix_and_fractional_window_boundary_are_excluded(self):
        prefix = "sqlalchemy.exc.OperationalError: server closed the connection unexpectedly\n[SQL: SELECT 1]\n"
        output = self._analyze_events(
            {"backend.log": prefix + self._traceback() + self._traceback("2026-09-03 08:54:10,901")},
            until="2026-09-03 08:54:10.900",
        )
        self.assertIn("sql_error_traceback_episodes count=1", output)

    def test_console_mirror_and_ui_restart_marker_do_not_duplicate_errors(self):
        hold = "db_connection_lifecycle duration_ms=5000 pid=1 role=worker non_sql_hold_ms=5000"
        colored = f"⚠️ [2026-09-03 08:54:09] WARNING  - lifecycle - {hold}\n"
        plain = f"2026-09-03 08:54:09,714 - lifecycle - WARNING - {hold}\n"
        error = self._traceback()
        restart = "\n===== UI START 2026-09-03T09:00:46.259211 service=celery_watchdog =====\n🩺 正在启动 Celery Watchdog...\n"
        output = self._analyze_events({
            "scheduler.log": plain + error,
            "celery/celery_watchdog.log": colored + plain + error + restart,
        })
        self.assertIn("events connection_held=1 server_closed=1", output)
        self.assertIn("sql_error_traceback_episodes count=1", output)

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

    def test_parses_colored_console_request_start_and_end(self):
        analyzer = load_analyzer()

        start = analyzer.REQUEST_START.search(
            "🟢 [2026-09-24 10:18:40] INFO     - middleware.request_logger - "
            "🌐 [07585b10] GET /api/mp3-video-workflows/audio-assets - IP: 127.0.0.1"
        )
        end = analyzer.REQUEST_END.search(
            "🟢 [2026-09-24 10:18:46] INFO     - middleware.request_logger - "
            "✅ [07585b10] 200 - 5848.14ms"
        )

        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertEqual("/api/mp3-video-workflows/audio-assets", start.group("path"))
        self.assertEqual("5848.14", end.group("duration"))

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

    def test_main_reports_task_guard_idle_transaction_observations(self):
        output = self._analyze_events({
            "scheduler.log": (
                "2026-09-03 08:54:10,100 - runtime_guard - WARNING - "
                "[TASK_GUARD] idle-in-transaction offenders count=2 details="
                "[{\"pid\":11,\"xact_age_seconds\":0.779,\"idle_age_seconds\":0.538},"
                "{\"pid\":12,\"xact_age_seconds\":0.106,\"idle_age_seconds\":-0.01}]\n"
            )
        })

        self.assertIn(
            "idle_in_transaction_observations episodes=1 max_connections=2 "
            "max_xact_age_seconds=0.779 max_idle_age_seconds=0.538",
            output,
        )

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

    def test_discovers_independent_scheduler_hold_without_double_counting_mirror(self):
        analyzer = load_analyzer()
        record = (
            "2026-09-18 01:36:50,741 - WARNING - database connection held too long "
            "duration_ms=6205.43 pid=29166 role=scheduler "
            "process=run_bilibili_intelligence_scheduler.py request_id=unknown "
            "checkout_origin=services/bilibili_intelligence_scheduler_service.py:57:load_schedule_config "
            "sql_count=2 sql_total_ms=6145.33 sql_max_ms=6118.16 non_sql_hold_ms=60.1\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            logs.mkdir()
            (logs / "bilibili_intelligence_scheduler.log").write_text(record)
            (logs / "scheduler.log").write_text(record)
            (logs / "pgbouncer.log").write_text(record)
            output = io.StringIO()
            with patch("sys.argv", [str(SCRIPT_PATH), "--since", "2026-09-18 00:00:00", "--until", "2026-09-18 06:00:00"]), patch.object(analyzer, "ROOT", root), patch.object(analyzer, "SLOW_LOG", logs / "slow_query.log"), patch.object(analyzer, "DEFAULT_EVENT_LOGS", (logs / "scheduler.log",)), redirect_stdout(output):
                self.assertEqual(analyzer.main(), 0)
        self.assertIn("connection_holds count=1", output.getvalue())
        self.assertIn("sql_total_ms=6145.33 non_sql_total_ms=60.10", output.getvalue())
        self.assertIn("connection_holds_query_correlated count=1", output.getvalue())

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

    def test_classifies_pgbouncer_query_wait_timeout_separately(self):
        analyzer = load_analyzer()

        self.assertEqual(
            {"query_wait_timeout"},
            analyzer.pgbouncer_event_types(
                "LOG C-0x1: closing because: query_wait_timeout (age=15s)"
            ),
        )

    def test_classifies_pgbouncer_server_crash_and_tls_receive_error(self):
        analyzer = load_analyzer()

        self.assertEqual(
            {"server_conn_crashed"},
            analyzer.pgbouncer_event_types(
                "LOG S-0x1: closing because: server conn crashed? (age=5s)"
            ),
        )
        self.assertEqual(
            {"tls_receive_error"},
            analyzer.pgbouncer_event_types(
                "WARNING tls_sbufio_recv: read failed: Can't assign requested address"
            ),
        )

    def test_detects_proxy_tun_source_on_new_server_connection(self):
        analyzer = load_analyzer()

        fallback = (
            "LOG S-0x1: db/user@100.92.99.39:5432 new connection to server "
            "(from 198.18.0.1:63433)"
        )
        tailscale = (
            "LOG S-0x2: db/user@100.92.99.39:5432 new connection to server "
            "(from 100.96.3.12:62169)"
        )

        self.assertEqual("198.18.0.1", analyzer.pgbouncer_server_source(fallback))
        self.assertTrue(analyzer.is_proxy_tun_source("198.18.0.1"))
        self.assertEqual("100.96.3.12", analyzer.pgbouncer_server_source(tailscale))
        self.assertFalse(analyzer.is_proxy_tun_source("100.96.3.12"))

    def test_quantifies_pgbouncer_connection_churn_per_window_hour(self):
        analyzer = load_analyzer()
        since = datetime(2026, 8, 29, 10, 0, 0)
        until = datetime(2026, 8, 29, 11, 0, 0)

        summary = analyzer.pgbouncer_connection_churn(
            [
                datetime(2026, 8, 29, 10, 0, 0),
                datetime(2026, 8, 29, 10, 30, 0),
            ],
            since=since,
            until=until,
        )

        self.assertEqual(2, summary["connections"])
        self.assertEqual(1.0, summary["window_hours"])
        self.assertEqual(2.0, summary["connections_per_window_hour"])

    def test_reports_pgbouncer_route_fallback_and_failure_categories(self):
        analyzer = load_analyzer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            logs.mkdir()
            (logs / "pgbouncer.log").write_text(
                "2026-08-14 13:07:08.239 LOG S-0x1: new connection to server "
                "(from 198.18.0.1:63433)\n"
                "2026-08-14 13:07:10.904 LOG C-0x2: closing because: "
                "query_wait_timeout (age=15s)\n"
                "2026-08-14 13:07:13.242 LOG S-0x1: closing because: "
                "server conn crashed? (age=5s)\n"
                "2026-08-14 13:07:54.312 WARNING tls_sbufio_recv: read failed: "
                "Can't assign requested address\n",
                encoding="utf-8",
            )
            analyzer.ROOT = root
            analyzer.SLOW_LOG = logs / "slow_query.log"
            analyzer.DEFAULT_EVENT_LOGS = ()
            output = io.StringIO()
            with patch(
                "sys.argv",
                [
                    "analyze_db_performance_logs.py",
                    "--since",
                    "2026-08-14 00:00:00",
                    "--until",
                    "2026-08-15 00:00:00",
                ],
            ), redirect_stdout(output):
                analyzer.main()

        rendered = output.getvalue()
        self.assertIn("query_wait_timeout=1", rendered)
        self.assertIn("server_conn_crashed=1", rendered)
        self.assertIn("tls_receive_error=1", rendered)
        self.assertIn(
            "pgbouncer_server_source local_address=198.18.0.1 connections=1",
            rendered,
        )
        self.assertIn("pgbouncer_proxy_tun_fallback connections=1", rendered)

    def test_parses_native_celery_bracket_timestamp(self):
        analyzer = load_analyzer()

        match = analyzer.TIMESTAMP.match(
            "[2026-07-30 04:15:55,872: WARNING/MainProcess] database error"
        )

        self.assertIsNotNone(match)
        self.assertEqual("2026-07-30 04:15:55,872", match.group("value"))

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

    def test_explicit_shell_process_overrides_shared_managed_log_path(self):
        analyzer = load_analyzer()

        self.assertTrue(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "unknown", "process": "-"},
                origin="unknown",
                source_path=analyzer.ROOT / "logs" / "scheduler.log",
            )
        )
        self.assertFalse(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "unknown", "process": "unknown"},
                origin="unknown",
                source_path=analyzer.ROOT / "logs" / "celery" / "celery_watchdog.log",
            )
        )
        self.assertTrue(
            analyzer.is_adhoc_diagnostic_context(
                {"role": "unknown", "process": "-"},
                origin="unknown",
                source_path=analyzer.ROOT / "logs" / "publication_tasks_query.log",
            )
        )


if __name__ == "__main__":
    unittest.main()
