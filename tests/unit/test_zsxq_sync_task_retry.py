import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tasks import zsxq_sync  # noqa: E402


class ZsxqSyncTaskRetryTest(unittest.TestCase):
    def test_server_closed_connection_is_retryable(self):
        exc = Exception("server closed the connection unexpectedly before or while processing the request")

        self.assertTrue(zsxq_sync._is_retryable_db_error(exc))

    def test_db_retry_count_and_delay_use_zsxq_sync_env(self):
        with patch.dict(
            os.environ,
            {
                "ZSXQ_SYNC_DB_RETRIES": "7",
                "ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS": "30",
            },
            clear=False,
        ):
            self.assertEqual(zsxq_sync._db_retry_count(), 7)
            self.assertEqual(zsxq_sync._db_retry_delay_seconds(0), 30)
            self.assertEqual(zsxq_sync._db_retry_delay_seconds(2), 90)


if __name__ == "__main__":
    unittest.main()
