import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.report_audio_workflow import _normalizer_model, status_payload  # noqa: E402


class ReportAudioWorkflowServiceTest(unittest.TestCase):
    def test_default_normalizer_model_is_deepseek_v4_pro(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_normalizer_model(), "deepseek-v4-pro")

    def test_normalizer_model_env_override_takes_precedence(self):
        with patch.dict(os.environ, {
            "REPORT_AUDIO_NORMALIZER_MODEL": "custom-normalizer",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_API_KEY": "test-key",
        }, clear=False):
            payload = status_payload()

        self.assertEqual(payload["normalizer_model"], "custom-normalizer")


if __name__ == "__main__":
    unittest.main()
