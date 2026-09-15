import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline.audio_cleaner_adapter import (  # noqa: E402
    DEFAULT_GAP_AUDIO,
)
from services.zsxq_service import ZsxqService  # noqa: E402


class ZsxqUploadRuntimeConfigTest(unittest.TestCase):
    def test_default_gap_audio_uses_persistent_project_runtime_path(self):
        self.assertEqual(
            PROJECT_ROOT / "runtime" / "content_pipeline" / "config" / "interval.mp3",
            DEFAULT_GAP_AUDIO,
        )

    def test_storage_upload_timeout_covers_slow_request_body_writes(self):
        response = Mock(status_code=200, text="{}")
        response.json.return_value = {}
        service = ZsxqService()

        with patch.dict(
            os.environ,
            {"ZSXQ_STORAGE_UPLOAD_TIMEOUT_SECONDS": "900"},
            clear=False,
        ):
            with patch("services.zsxq_service.requests.post", return_value=response) as post:
                service.upload_to_storage("token", "large.mp3", b"audio", "audio/mpeg")

        self.assertEqual(900, post.call_args.kwargs["timeout"])


if __name__ == "__main__":
    unittest.main()
