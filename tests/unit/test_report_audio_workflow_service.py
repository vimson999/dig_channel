import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import report_audio_workflow as report_audio_service  # noqa: E402
from services.report_audio_workflow import (  # noqa: E402
    ReportAudioWorkflowError,
    _normalizer_model,
    normalize_for_tts,
    status_payload,
    structured_result_quality_issue,
)


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

    def test_local_fallback_strips_gemini_capture_artifacts(self):
        dirty_text = (
            "研报pdf解析视频文案 自定义 Gem 研报pdf解析视频文案说 "
            "【研报气质】 颠覆型 【Hook策略】 反常识提问型 【结尾情绪基调】 聚焦式 "
            "如果我告诉你，中东的紧张局势全面降温，黄金非但不会默默，反而会迎来新一轮爆发，你信吗？ "
            "PDF + 1 今天我们就来拆解这份报告。"
        )

        with patch.object(report_audio_service, "_deepseek_normalize", return_value=None):
            payload = normalize_for_tts(dirty_text)

        script = payload["final_script_for_tts"]
        self.assertEqual(payload["provider"], "local_fallback")
        self.assertTrue(script.startswith("如果我告诉你"))
        self.assertNotIn("自定义 Gem", script)
        self.assertNotIn("研报pdf解析视频文案", script)
        self.assertNotIn("PDF + 1", script)

    def test_structured_result_quality_detects_cached_capture_artifacts(self):
        issue = structured_result_quality_issue({
            "final_script_for_tts": "研报pdf解析视频文案 自定义 Gem 研报pdf解析视频文案说 这是一段正文。",
        })

        self.assertIn("页面", issue)

    def test_normalizer_rejects_unclean_capture_artifacts(self):
        payload = {
            "final_script_for_tts": "这是一段正文，但中间混入了自定义 Gem 这种页面残留，不能交给豆包朗读。",
        }

        with patch.object(report_audio_service, "_deepseek_normalize", return_value=payload):
            with self.assertRaisesRegex(ReportAudioWorkflowError, "不可用于朗读"):
                normalize_for_tts("原始 Gemini 文本")


if __name__ == "__main__":
    unittest.main()
