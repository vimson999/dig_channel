import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.youtube_api import YouTubeAPI, YouTubeAPIError


class YouTubeApiErrorDiagnosticsTest(unittest.TestCase):
    def test_http_error_preserves_safe_reason_without_api_key(self):
        api = YouTubeAPI(api_key="super-secret-key")
        response = Mock(status_code=403)
        response.json.return_value = {
            "error": {
                "message": "The request cannot be completed because you have exceeded your quota.",
                "errors": [{"reason": "quotaExceeded"}],
            }
        }
        response.raise_for_status.side_effect = requests.HTTPError(
            "403 for https://www.googleapis.com/youtube/v3/channels?key=super-secret-key",
            response=response,
        )
        api.session.get = Mock(return_value=response)

        with self.assertRaises(YouTubeAPIError) as captured:
            api._make_request("channels", {"part": "statistics", "id": "UC-test"})

        message = str(captured.exception)
        self.assertIn("status=403", message)
        self.assertIn("reason=quotaExceeded", message)
        self.assertNotIn("super-secret-key", message)
        self.assertNotIn("?key=", message)

    def test_channel_video_failure_exposes_safe_upstream_reason(self):
        api = YouTubeAPI(api_key="test-key")
        api._get_channel_uploads_playlist = Mock(return_value=None)
        api._set_last_error("API HTTP failure endpoint=channels status=403 reason=quotaExceeded")

        result = api.get_channel_videos("UC-test")

        self.assertFalse(result["success"])
        self.assertIn("reason=quotaExceeded", result["message"])
        self.assertIsNone(api.consume_last_error())


if __name__ == "__main__":
    unittest.main()
