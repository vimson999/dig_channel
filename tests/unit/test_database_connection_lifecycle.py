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
        ):
            database_logger._pool_checkout(None, record, None)
            database_logger._pool_checkin(None, record)

        lifecycle_logger.warning.assert_called_once()
        self.assertNotIn(database_logger._connection_checkout_key, record.info)
        self.assertEqual(
            lifecycle_logger.warning.call_args.args[1],
            6250.0,
        )

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
        ):
            database_logger._pool_checkout(None, record, None)
            database_logger._pool_checkin(None, record)

        lifecycle_logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
