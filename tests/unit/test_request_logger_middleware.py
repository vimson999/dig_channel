import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from celery.exceptions import Retry


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from middleware import request_logger  # noqa: E402


class RequestLoggerMiddlewareTest(unittest.TestCase):
    def test_celery_retry_is_not_logged_as_request_error(self):
        middleware = request_logger.RequestLoggerMiddleware()

        with (
            patch.object(request_logger.logger, "error") as error,
            patch.object(request_logger.logger, "info") as info,
        ):
            middleware.teardown_request(Retry())

        error.assert_not_called()
        info.assert_called_once_with("Celery task retry scheduled")


if __name__ == "__main__":
    unittest.main()
