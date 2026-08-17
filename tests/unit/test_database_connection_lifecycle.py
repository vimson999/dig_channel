import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from middleware import database_logger  # noqa: E402


class DatabaseConnectionLifecycleTest(unittest.TestCase):
    def test_long_checkout_is_reported_on_checkin(self):
        record = SimpleNamespace(info={})
        lifecycle_logger = Mock()

        with patch.object(
            database_logger.time,
            "monotonic",
            side_effect=(100.0, 106.25),
        ), patch.object(
            database_logger,
            "setup_colored_logger",
            return_value=lifecycle_logger,
        ), patch.dict(
            database_logger.os.environ,
            {"DB_CONNECTION_HOLD_WARN_SECONDS": "5"},
        ), patch.object(
            database_logger.DatabaseLoggerMiddleware,
            "_request_id",
            return_value="request-1",
        ), patch.object(
            database_logger.DatabaseLoggerMiddleware,
            "_celery_task_context",
            return_value=("task-1", "tasks.fixture"),
        ), patch.object(
            database_logger.DatabaseLoggerMiddleware,
            "_query_origin",
            return_value="tasks/fixture.py:12:run",
        ):
            database_logger._pool_checkout(None, record, None)
            record.info[database_logger._connection_sql_count_key] = 2
            record.info[database_logger._connection_sql_total_ms_key] = 1250.0
            record.info[database_logger._connection_sql_max_ms_key] = 900.0
            database_logger._pool_checkin(None, record)

        lifecycle_logger.warning.assert_called_once()
        self.assertNotIn(database_logger._connection_checkout_key, record.info)
        self.assertEqual(
            lifecycle_logger.warning.call_args.args[1],
            6250.0,
        )
        message = lifecycle_logger.warning.call_args.args[0]
        values = lifecycle_logger.warning.call_args.args
        self.assertIn("request_id=%s", message)
        self.assertIn("sql_total_ms=%s", message)
        self.assertIn("non_sql_hold_ms=%s", message)
        self.assertIn("request-1", values)
        self.assertIn("task-1", values)
        self.assertIn("tasks/fixture.py:12:run", values)
        self.assertIn(1250.0, values)
        self.assertIn(5000.0, values)

    def test_short_checkout_is_not_reported(self):
        record = SimpleNamespace(info={})
        lifecycle_logger = Mock()

        with patch.object(
            database_logger.time,
            "monotonic",
            side_effect=(100.0, 100.25),
        ), patch.object(
            database_logger,
            "setup_colored_logger",
            return_value=lifecycle_logger,
        ), patch.object(
            database_logger.DatabaseLoggerMiddleware,
            "_query_origin",
            return_value="tasks/fixture.py:12:run",
        ):
            database_logger._pool_checkout(None, record, None)
            database_logger._pool_checkin(None, record)

        lifecycle_logger.warning.assert_not_called()

    def test_sql_duration_is_aggregated_for_current_checkout(self):
        connection = SimpleNamespace(info={})

        database_logger.DatabaseLoggerMiddleware._record_checkout_sql_duration(
            connection,
            125.5,
        )
        database_logger.DatabaseLoggerMiddleware._record_checkout_sql_duration(
            connection,
            400.25,
        )

        self.assertEqual(connection.info[database_logger._connection_sql_count_key], 2)
        self.assertEqual(
            connection.info[database_logger._connection_sql_total_ms_key],
            525.75,
        )
        self.assertEqual(
            connection.info[database_logger._connection_sql_max_ms_key],
            400.25,
        )

    def test_failed_sql_duration_is_aggregated_for_current_checkout(self):
        connection = SimpleNamespace(info={"query_start_time": [100.0]})
        exception_context = SimpleNamespace(
            connection=connection,
            statement="SELECT 1",
            original_exception=RuntimeError("query timeout"),
        )
        middleware = database_logger.DatabaseLoggerMiddleware()

        with patch.object(database_logger.time, "time", return_value=120.0), patch.object(
            database_logger.logger,
            "exception",
        ):
            middleware._handle_error(exception_context)

        self.assertEqual(
            connection.info[database_logger._connection_sql_count_key],
            1,
        )
        self.assertEqual(
            connection.info[database_logger._connection_sql_total_ms_key],
            20000.0,
        )
        self.assertEqual(
            connection.info[database_logger._connection_sql_max_ms_key],
            20000.0,
        )


if __name__ == "__main__":
    unittest.main()
