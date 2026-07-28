import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class RuntimeInitializationIdempotencyTest(unittest.TestCase):
    def test_backend_restart_requires_old_listener_to_stop_and_new_pid_to_own_port(self):
        project_root = Path(__file__).resolve().parents[2]
        restart = (project_root / "scripts" / "dev" / "restart-dev-stack.sh").read_text(
            encoding="utf-8"
        )
        stop = (project_root / "scripts" / "dev" / "stop-backend.sh").read_text(
            encoding="utf-8"
        )
        run = (project_root / "scripts" / "dev" / "run-backend.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"$SCRIPT_DIR/stop-backend.sh"\n', restart)
        self.assertNotIn('"$SCRIPT_DIR/stop-backend.sh" || true\n\n', restart)
        self.assertIn('[ "$listener_pid" != "$backend_pid" ]', restart)
        self.assertIn("拒绝继续启动", restart)
        self.assertIn("descendant_pids", stop)
        self.assertIn("BACKEND_STOP_TIMEOUT_SECONDS", stop)
        self.assertIn("BACKEND_PID_FILE", stop)
        self.assertIn("exec python app.py", run)

    def test_background_stop_does_not_treat_residual_check_failure_as_clean(self):
        project_root = Path(__file__).resolve().parents[2]
        stop_all = (project_root / "scripts" / "dev" / "stop-all-background-jobs.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('if ! remaining="$(ps -axo pid=,command= | awk', stop_all)
        self.assertIn("拒绝报告重启前环境已清理", stop_all)
        self.assertNotIn("index($0, project) && (\n", stop_all)

    def test_targeted_celery_stop_scripts_match_worker_command_not_controller_shell(self):
        project_root = Path(__file__).resolve().parents[2]
        expectations = {
            "stop-celery-report-audio-workflow.sh": " -Q report_audio_workflow( |$)",
            "stop-celery-doubao-browser-tts.sh": " -Q doubao_browser_tts( |$)",
        }
        for filename, queue_pattern in expectations.items():
            text = (project_root / "scripts" / "dev" / filename).read_text(encoding="utf-8")
            self.assertIn("/\\/venv\\/bin\\/celery/", text)
            self.assertIn(queue_pattern, text)
            self.assertNotIn("grep 'celery'", text)

        generic = (project_root / "scripts" / "dev" / "stop-celery.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('CELERY_BIN="$PROJECT_ROOT/venv/bin/celery"', generic)
        self.assertIn('index($0, celery)', generic)
        self.assertIn('/ worker( |$)/', generic)
        self.assertNotIn("grep 'celery'", generic)

    def test_performance_monitor_starts_only_one_thread_per_process(self):
        from middleware.performance_monitor import PerformanceMonitor

        monitor = PerformanceMonitor()
        thread = MagicMock()
        thread.is_alive.return_value = True
        with patch("middleware.performance_monitor.threading.Thread", return_value=thread) as thread_factory:
            self.assertTrue(monitor._start_system_monitor())
            self.assertFalse(monitor._start_system_monitor())

        thread_factory.assert_called_once()
        thread.start.assert_called_once()

    def test_log_maintenance_starts_only_one_thread_per_process(self):
        from utils import log_manager as module

        thread = MagicMock()
        thread.is_alive.return_value = True
        with patch.object(module, "_maintenance_pid", None), patch.object(
            module, "_maintenance_thread", None
        ), patch("utils.log_manager.threading.Thread", return_value=thread) as thread_factory:
            self.assertTrue(module.schedule_log_maintenance())
            self.assertFalse(module.schedule_log_maintenance())

        thread_factory.assert_called_once()
        thread.start.assert_called_once()

    def test_application_logging_is_configured_once_per_process(self):
        from utils import log_manager as module

        with patch.object(module, "_application_logging_pid", None), patch.object(
            module.log_manager, "setup_timed_rotating_logger"
        ) as timed_setup, patch.object(module.log_manager, "setup_rotating_logger") as rotating_setup:
            self.assertTrue(module.setup_application_logging({}))
            self.assertFalse(module.setup_application_logging({}))

        self.assertEqual(timed_setup.call_count, 2)
        self.assertEqual(rotating_setup.call_count, 7)

    def test_database_listeners_are_registered_once(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        middleware = DatabaseLoggerMiddleware()
        app = MagicMock()
        registered = set()

        def contains(_target, event_name, callback):
            return (event_name, callback) in registered

        def listen(_target, event_name, callback):
            registered.add((event_name, callback))

        with patch("middleware.database_logger.event.contains", side_effect=contains), patch(
            "middleware.database_logger.event.listen", side_effect=listen
        ) as event_listen:
            middleware.init_app(app)
            middleware.init_app(app)

        self.assertEqual(event_listen.call_count, 5)
        self.assertEqual(
            {event_name for event_name, _callback in registered},
            {
                "before_cursor_execute",
                "after_cursor_execute",
                "handle_error",
                "checkout",
                "checkin",
            },
        )

    def test_database_error_diagnostic_tolerates_invalidated_connection_info(self):
        from middleware import database_logger as module

        class InvalidatedConnection:
            dialect = SimpleNamespace(name="postgresql")

            @property
            def info(self):
                raise RuntimeError("connection is invalidated")

        context = SimpleNamespace(
            connection=InvalidatedConnection(),
            statement=(
                "SELECT publication_tasks.result FROM publication_tasks "
                "WHERE publication_tasks.id = %(id_1)s"
            ),
            original_exception=RuntimeError("server closed the connection unexpectedly"),
        )
        middleware = module.DatabaseLoggerMiddleware()

        with patch.object(middleware, "_log_publication_task_query") as log_query, patch.object(
            module.logger,
            "exception",
        ) as log_exception:
            middleware._handle_error(context)

        log_query.assert_called_once()
        log_exception.assert_not_called()

    def test_slow_completed_sql_is_logged_as_success_and_keeps_its_update_table(self):
        from middleware import database_logger as module

        middleware = module.DatabaseLoggerMiddleware()
        connection = SimpleNamespace(
            info={"query_start_time": [0.0]},
            dialect=SimpleNamespace(name="postgresql"),
        )
        cursor = SimpleNamespace(rowcount=84)
        with patch.object(module, "g", SimpleNamespace(request_id="slow-success")), patch.object(
            module.time,
            "time",
            return_value=6.5,
        ), patch.object(module, "log_database_operation") as operation, patch.object(
            middleware,
            "_log_slow_query",
        ):
            middleware._after_cursor_execute(
                connection,
                cursor,
                "UPDATE zsxq_attachments SET name=%(name)s WHERE id=%(id)s",
                {},
                None,
                False,
            )

        operation.assert_called_once_with(
            "UPDATE",
            "ZSXQ_ATTACHMENTS",
            True,
            "耗时: 6500.0ms, 影响行数: 84",
        )

    def test_publication_task_query_guard_classifies_multirow_json_payload(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        statement = """
            SELECT publication_tasks.id, publication_tasks.params, publication_tasks.result
            FROM publication_tasks
            WHERE publication_tasks.feature = %(feature_1)s
            ORDER BY publication_tasks.created_at DESC
        """
        diagnostic = DatabaseLoggerMiddleware._publication_task_diagnostic(
            statement,
            duration_ms=120,
        )

        self.assertIn("multirow_payload", diagnostic["categories"])
        self.assertEqual(("params", "result"), diagnostic["payload_columns"])
        self.assertFalse(diagnostic["point_lookup"])

    def test_publication_task_query_guard_allows_fast_detail_payload(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        statement = """
            SELECT publication_tasks.id, publication_tasks.params, publication_tasks.result
            FROM publication_tasks
            WHERE publication_tasks.id = %(id_1)s
        """
        diagnostic = DatabaseLoggerMiddleware._publication_task_diagnostic(
            statement,
            duration_ms=120,
        )

        self.assertIsNone(diagnostic)

    def test_publication_task_query_guard_classifies_json_predicate_and_slow_point_lookup(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        json_statement = """
            SELECT publication_tasks.id
            FROM publication_tasks
            WHERE publication_tasks.params ->> %(params_1)s = %(value_1)s
        """
        payload_statement = """
            SELECT publication_tasks.id, publication_tasks.result
            FROM publication_tasks
            WHERE publication_tasks.id = %(id_1)s
        """

        json_diagnostic = DatabaseLoggerMiddleware._publication_task_diagnostic(
            json_statement,
            duration_ms=40,
        )
        payload_diagnostic = DatabaseLoggerMiddleware._publication_task_diagnostic(
            payload_statement,
            duration_ms=750,
        )

        self.assertIn("json_predicate", json_diagnostic["categories"])
        self.assertIn("slow_payload", payload_diagnostic["categories"])
        self.assertIn("slow", payload_diagnostic["categories"])

    def test_publication_task_query_guard_does_not_treat_json_scalar_projection_as_payload(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        statement = """
            SELECT publication_tasks.id,
                   CAST(publication_tasks.params ->> %(params_1)s AS VARCHAR) AS task_title,
                   CAST(publication_tasks.result ->> %(result_1)s AS VARCHAR) AS video_path
            FROM publication_tasks
            WHERE publication_tasks.feature = %(feature_1)s
            ORDER BY publication_tasks.created_at DESC
        """
        diagnostic = DatabaseLoggerMiddleware._publication_task_diagnostic(
            statement,
            duration_ms=750,
        )

        self.assertEqual(("slow",), diagnostic["categories"])
        self.assertEqual((), diagnostic["payload_columns"])

    def test_publication_task_query_guard_classifies_slow_json_update(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        statement = """
            UPDATE publication_tasks
            SET current_step=%(current_step)s, progress=%(progress)s,
                result=%(result)s, updated_at=%(updated_at)s
            WHERE publication_tasks.id = %(id_1)s
              AND publication_tasks.status != %(status_1)s
        """
        diagnostic = DatabaseLoggerMiddleware._publication_task_diagnostic(
            statement,
            duration_ms=750,
        )

        self.assertIn("slow_payload_write", diagnostic["categories"])
        self.assertIn("slow_payload", diagnostic["categories"])
        self.assertEqual(("result",), diagnostic["payload_columns"])
        self.assertTrue(diagnostic["point_lookup"])

    def test_publication_task_query_guard_recognizes_unqualified_update_id(self):
        from middleware.database_logger import DatabaseLoggerMiddleware

        statement = """
            UPDATE public.publication_tasks
            SET result = COALESCE(result, '{}'::jsonb) || CAST(:patch AS jsonb),
                updated_at = NOW()
            WHERE id = :task_id
              AND status IS DISTINCT FROM 'canceled'
        """

        fast = DatabaseLoggerMiddleware._publication_task_diagnostic(
            statement,
            duration_ms=120,
        )
        slow = DatabaseLoggerMiddleware._publication_task_diagnostic(
            statement,
            duration_ms=750,
        )

        self.assertIsNone(fast)
        self.assertTrue(slow["point_lookup"])
        self.assertNotIn("multirow_payload", slow["categories"])
        self.assertIn("slow_payload_write", slow["categories"])

    def test_slow_query_origin_reports_backend_callsite(self):
        import traceback

        from middleware import database_logger as module

        frames = [
            traceback.FrameSummary(
                str(module._backend_root / "tasks" / "gemini_browser.py"),
                123,
                "update_progress",
            ),
            traceback.FrameSummary(module.__file__, 180, "_query_origin"),
        ]
        with patch.object(module.traceback, "extract_stack", return_value=frames):
            origin = module.DatabaseLoggerMiddleware._query_origin()

        self.assertEqual("tasks/gemini_browser.py:123:update_progress", origin)

    def test_slow_query_origin_skips_generic_task_loader(self):
        import traceback

        from middleware import database_logger as module

        frames = [
            traceback.FrameSummary(
                str(module._backend_root / "tasks" / "report_audio_workflow.py"),
                740,
                "_wait_child",
            ),
            traceback.FrameSummary(
                str(module._backend_root / "tasks" / "report_audio_workflow.py"),
                177,
                "_load_task",
            ),
            traceback.FrameSummary(module.__file__, 300, "_query_origin"),
        ]
        with patch.object(module.traceback, "extract_stack", return_value=frames):
            origin = module.DatabaseLoggerMiddleware._query_origin()

        self.assertEqual("tasks/report_audio_workflow.py:740:_wait_child", origin)

    def test_worker_process_does_not_start_web_background_threads(self):
        from app import _start_web_background_threads

        app = MagicMock()
        app.config = {"TESTING": False}
        with patch.dict(os.environ, {"DB_PROCESS_ROLE": "worker"}, clear=False):
            self.assertFalse(_start_web_background_threads(app))


if __name__ == "__main__":
    unittest.main()
